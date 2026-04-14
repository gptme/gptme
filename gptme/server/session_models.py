"""Session data models — ToolStatus, ToolExecution, ConversationSession, SessionManager.

Extracted from api_v2_sessions.py to separate data definitions from
execution logic and Flask route handlers.
"""

import logging
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING

from ..hooks import HookType, trigger_hook
from ..session import BaseSession
from ..tools import ToolUse
from .api_v2_common import EventType

if TYPE_CHECKING:
    from .acp_session_runtime import AcpSessionRuntime

logger = logging.getLogger(__name__)


class ToolStatus(Enum):
    """Status of a tool execution."""

    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class ToolExecution:
    """Tracks a tool execution."""

    tool_id: str
    tooluse: ToolUse
    status: ToolStatus = ToolStatus.PENDING
    auto_confirm: bool = False


@dataclass
class ConversationSession(BaseSession):
    """Session for a conversation.

    Extends BaseSession with server-specific fields for event streaming,
    tool execution tracking, and client management.

    Inherited from BaseSession:
        id: str - Session identifier
        conversation_id: str | None - Conversation/log identifier
        active: bool - Whether session is active
        created_at: datetime - Session creation timestamp
        last_activity: datetime - Last activity timestamp

    Server-specific fields:
        generating: bool - Whether LLM is currently generating
        events: list - Event queue for SSE streaming
        pending_tools: dict - Tools awaiting confirmation
        auto_confirm_count: int - Auto-confirm counter
        clients: set - Connected client IDs
        event_flag: Event - Threading event for notifications
    """

    # Server-specific fields (all have defaults, required for dataclass inheritance)
    generating: bool = False
    generating_since: datetime | None = (
        None  # When generation started (for stuck detection)
    )
    last_error: str | None = None
    events: list[EventType] = field(default_factory=list)
    _events_offset: int = 0  # number of events trimmed from front of list
    pending_tools: dict[str, ToolExecution] = field(default_factory=dict)
    auto_confirm_count: int = 0
    clients: set[str] = field(default_factory=set)
    event_flag: threading.Event = field(default_factory=threading.Event)
    # Lock for atomic check-and-set of the generating flag in /step.
    # Prevents concurrent requests from both reading False before either writes True.
    step_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ACP-backed subprocess session (opt-in via use_acp=True in step request)
    use_acp: bool = False
    acp_runtime: "AcpSessionRuntime | None" = field(default=None, repr=False)
    # Index of the last user message processed through ACP mode.
    # Prevents duplicate /step calls from re-sending the same user message.
    acp_last_user_msg_index: int = -1

    # Maximum events to keep in memory per session before trimming.
    # Each LLM token generates one event, so a 10K-token response = 10K events.
    # At ~200 bytes/event, 10K events ≈ 2MB. We trim to keep_last when exceeded.
    _MAX_EVENTS = 10_000
    _KEEP_EVENTS = 1_000

    @property
    def events_count(self) -> int:
        """Absolute event count (including trimmed events)."""
        return self._events_offset + len(self.events)

    def get_events_since(self, abs_index: int) -> list[EventType]:
        """Get events from an absolute index (accounting for trimmed events)."""
        rel_index = max(0, abs_index - self._events_offset)
        return self.events[rel_index:]

    def trim_events(self) -> None:
        """Trim old events when the list exceeds _MAX_EVENTS.

        Only trims when no clients are connected to avoid breaking
        in-flight SSE streams that reference absolute indices.
        """
        if len(self.events) <= self._MAX_EVENTS or self.clients:
            return
        trim_count = len(self.events) - self._KEEP_EVENTS
        self._events_offset += trim_count
        self.events = self.events[trim_count:]


class SessionManager:
    """Manages conversation sessions.

    Thread-safe: all access to ``_sessions`` and ``_conversation_sessions``
    is serialized through ``_lock``.  Long-running side-effects (hook
    triggers, ACP runtime cleanup) run outside the lock to avoid blocking
    concurrent readers.
    """

    _sessions: dict[str, ConversationSession] = {}
    _conversation_sessions: dict[str, set[str]] = defaultdict(set)
    _lock = threading.Lock()

    @classmethod
    def create_session(cls, conversation_id: str) -> ConversationSession:
        """Create a new session for a conversation."""
        session_id = str(uuid.uuid4())
        session = ConversationSession(id=session_id, conversation_id=conversation_id)
        with cls._lock:
            cls._sessions[session_id] = session
            cls._conversation_sessions[conversation_id].add(session_id)
        return session

    @classmethod
    def get_session(cls, session_id: str) -> ConversationSession | None:
        """Get a session by ID."""
        with cls._lock:
            return cls._sessions.get(session_id)

    @classmethod
    def get_all_sessions(cls) -> list[tuple[str, ConversationSession]]:
        """Return a snapshot of all (session_id, session) pairs."""
        with cls._lock:
            return list(cls._sessions.items())

    @classmethod
    def get_sessions_for_conversation(
        cls, conversation_id: str
    ) -> list[ConversationSession]:
        """Get all sessions for a conversation."""
        with cls._lock:
            return [
                cls._sessions[sid]
                for sid in list(cls._conversation_sessions.get(conversation_id, set()))
                if sid in cls._sessions
            ]

    @classmethod
    def add_event(cls, conversation_id: str, event: EventType) -> None:
        """Add an event to all sessions for a conversation."""
        sessions = cls.get_sessions_for_conversation(conversation_id)
        for session in sessions:
            session.events.append(event)
            session.trim_events()
            session.touch()
            session.event_flag.set()

    _STUCK_GENERATING_TIMEOUT_MINUTES = 10

    @classmethod
    def clean_inactive_sessions(cls, max_age_minutes: int = 60) -> None:
        """Clean up inactive sessions.

        Also detects sessions stuck in generating=True state: if a session has
        been generating for longer than _STUCK_GENERATING_TIMEOUT_MINUTES, it is
        force-cleaned to prevent permanent resource leaks.

        Removal is performed atomically under a single lock acquisition to
        prevent a TOCTOU race where a concurrent ``/step`` could start
        generating on a session between the staleness check and its removal.
        Side-effects (hook triggers, ACP cleanup) run after the lock is
        released.
        """
        now = datetime.now(tz=timezone.utc)
        cutoff = now - timedelta(minutes=max_age_minutes)
        stuck_cutoff = now - timedelta(minutes=cls._STUCK_GENERATING_TIMEOUT_MINUTES)

        # Collect post-lock work: (conversation_id, is_last, acp_runtime)
        deferred: list[tuple[str, bool, AcpSessionRuntime | None]] = []

        with cls._lock:
            to_remove: list[str] = []
            for session_id, session in cls._sessions.items():
                if session.last_activity < cutoff and not session.generating:
                    to_remove.append(session_id)
                elif (
                    session.generating
                    and session.generating_since is not None
                    and session.generating_since < stuck_cutoff
                ):
                    logger.warning(
                        "Force-cleaning stuck session %s (generating since %s, "
                        "exceeded %d min timeout)",
                        session_id,
                        session.generating_since.isoformat(),
                        cls._STUCK_GENERATING_TIMEOUT_MINUTES,
                    )
                    session.generating = False
                    to_remove.append(session_id)

            # Remove all identified sessions while still holding the lock.
            for session_id in to_remove:
                session = cls._sessions[session_id]
                conversation_id = session.conversation_id
                if conversation_id is None:
                    raise ValueError("Server sessions must have conversation_id")

                is_last = (
                    conversation_id in cls._conversation_sessions
                    and len(cls._conversation_sessions[conversation_id]) == 1
                    and session_id in cls._conversation_sessions[conversation_id]
                )

                if conversation_id in cls._conversation_sessions:
                    cls._conversation_sessions[conversation_id].discard(session_id)
                    if not cls._conversation_sessions[conversation_id]:
                        del cls._conversation_sessions[conversation_id]

                acp_rt = session.acp_runtime
                del cls._sessions[session_id]
                deferred.append((conversation_id, is_last, acp_rt))

        # Phase 2: outside lock — long-running side-effects
        for conversation_id, is_last, acp_rt in deferred:
            if is_last:
                try:
                    from ..logmanager import LogManager

                    manager = LogManager.load(conversation_id, lock=True)
                    logger.debug(
                        "Last session for conversation %s, triggering SESSION_END hook",
                        conversation_id,
                    )
                    if session_end_msgs := trigger_hook(
                        HookType.SESSION_END,
                        manager=manager,
                    ):
                        for msg in session_end_msgs:
                            manager.append(msg)
                except Exception as e:
                    logger.warning(f"Failed to trigger SESSION_END hook: {e}")

            if acp_rt is not None:
                from .session_step import close_acp_runtime_bg

                close_acp_runtime_bg(acp_rt)

    @classmethod
    def remove_session(cls, session_id: str) -> None:
        """Remove a session.

        Dict mutations happen under ``_lock``; hook triggers and ACP cleanup
        run after the lock is released to avoid blocking other threads.
        """
        # Phase 1: under lock — gather info and remove from dicts
        with cls._lock:
            if session_id not in cls._sessions:
                return
            session = cls._sessions[session_id]
            conversation_id = session.conversation_id
            if conversation_id is None:
                raise ValueError("Server sessions must have conversation_id")

            is_last_session = (
                conversation_id in cls._conversation_sessions
                and len(cls._conversation_sessions[conversation_id]) == 1
                and session_id in cls._conversation_sessions[conversation_id]
            )

            if conversation_id in cls._conversation_sessions:
                cls._conversation_sessions[conversation_id].discard(session_id)
                if not cls._conversation_sessions[conversation_id]:
                    del cls._conversation_sessions[conversation_id]

            acp_rt = session.acp_runtime
            del cls._sessions[session_id]

        # Phase 2: outside lock — long-running side-effects
        if is_last_session:
            try:
                from ..logmanager import LogManager

                manager = LogManager.load(conversation_id, lock=True)

                logger.debug(
                    f"Last session for conversation {conversation_id}, triggering SESSION_END hook"
                )
                if session_end_msgs := trigger_hook(
                    HookType.SESSION_END,
                    manager=manager,
                ):
                    for msg in session_end_msgs:
                        manager.append(msg)
            except Exception as e:
                logger.warning(f"Failed to trigger SESSION_END hook: {e}")

        if acp_rt is not None:
            from .session_step import close_acp_runtime_bg

            close_acp_runtime_bg(acp_rt)

    @classmethod
    def remove_all_sessions_for_conversation(cls, conversation_id: str) -> None:
        """Remove all sessions for a conversation."""
        for session in cls.get_sessions_for_conversation(conversation_id):
            cls.remove_session(session.id)
