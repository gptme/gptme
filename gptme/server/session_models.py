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
    events: list[EventType] = field(default_factory=list)
    pending_tools: dict[str, ToolExecution] = field(default_factory=dict)
    auto_confirm_count: int = 0
    clients: set[str] = field(default_factory=set)
    event_flag: threading.Event = field(default_factory=threading.Event)

    # ACP-backed subprocess session (opt-in via use_acp=True in step request)
    use_acp: bool = False
    acp_runtime: "AcpSessionRuntime | None" = field(default=None, repr=False)
    # Index of the last user message processed through ACP mode.
    # Prevents duplicate /step calls from re-sending the same user message.
    acp_last_user_msg_index: int = -1


class SessionManager:
    """Manages conversation sessions."""

    _sessions: dict[str, ConversationSession] = {}
    _conversation_sessions: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def create_session(cls, conversation_id: str) -> ConversationSession:
        """Create a new session for a conversation."""
        session_id = str(uuid.uuid4())
        session = ConversationSession(id=session_id, conversation_id=conversation_id)
        cls._sessions[session_id] = session
        cls._conversation_sessions[conversation_id].add(session_id)
        return session

    @classmethod
    def get_session(cls, session_id: str) -> ConversationSession | None:
        """Get a session by ID."""
        return cls._sessions.get(session_id)

    @classmethod
    def get_sessions_for_conversation(
        cls, conversation_id: str
    ) -> list[ConversationSession]:
        """Get all sessions for a conversation."""
        return [
            cls._sessions[sid]
            for sid in cls._conversation_sessions.get(conversation_id, set())
            if sid in cls._sessions
        ]

    @classmethod
    def add_event(cls, conversation_id: str, event: EventType) -> None:
        """Add an event to all sessions for a conversation."""
        for session in cls.get_sessions_for_conversation(conversation_id):
            session.events.append(event)
            session.touch()  # Update last_activity timestamp
            session.event_flag.set()  # Signal that new events are available

    @classmethod
    def clean_inactive_sessions(cls, max_age_minutes: int = 60) -> None:
        """Clean up inactive sessions."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=max_age_minutes)
        to_remove = []

        for session_id, session in cls._sessions.items():
            if session.last_activity < cutoff and not session.generating:
                to_remove.append(session_id)

        for session_id in to_remove:
            cls.remove_session(session_id)

    @classmethod
    def remove_session(cls, session_id: str) -> None:
        """Remove a session."""
        if session_id in cls._sessions:
            conversation_id = cls._sessions[session_id].conversation_id
            if conversation_id is None:
                raise ValueError("Server sessions must have conversation_id")

            # Trigger SESSION_END hook when removing the last session for a conversation
            is_last_session = (
                conversation_id in cls._conversation_sessions
                and len(cls._conversation_sessions[conversation_id]) == 1
                and session_id in cls._conversation_sessions[conversation_id]
            )

            if is_last_session:
                try:
                    # Load the conversation to trigger the hook
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
                            manager.append(
                                msg
                            )  # Just append, no notify needed during cleanup
                except Exception as e:
                    logger.warning(f"Failed to trigger SESSION_END hook: {e}")

            if conversation_id in cls._conversation_sessions:
                cls._conversation_sessions[conversation_id].discard(session_id)
                if not cls._conversation_sessions[conversation_id]:
                    del cls._conversation_sessions[conversation_id]

            # Close ACP runtime if present
            acp_rt = cls._sessions[session_id].acp_runtime
            if acp_rt is not None:
                # Function-level import to avoid circular dependency:
                # session_step imports session_models, so session_models
                # cannot import session_step at module level.
                from .session_step import close_acp_runtime_bg

                close_acp_runtime_bg(acp_rt)

            del cls._sessions[session_id]

    @classmethod
    def remove_all_sessions_for_conversation(cls, conversation_id: str) -> None:
        """Remove all sessions for a conversation."""
        for session in cls.get_sessions_for_conversation(conversation_id):
            cls.remove_session(session.id)
