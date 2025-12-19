"""
Shared session management module for gptme.

This module provides common session infrastructure used by both
the ACP agent and the server API for managing conversation sessions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .logmanager import LogManager

logger = logging.getLogger(__name__)


@dataclass
class BaseSession:
    """Base session class for chat interactions.

    Provides common session state shared across ACP and Server implementations.
    Server's ConversationSession extends this with additional server-specific fields.

    Attributes:
        id: Unique session identifier
        log: Optional LogManager for direct log access (used by ACP)
        conversation_id: Optional conversation/log ID for deferred loading (used by Server)
        active: Whether session is active
        created_at: Session creation timestamp
        last_activity: Last activity timestamp (updated via touch())
    """

    id: str
    log: LogManager | None = None
    conversation_id: str | None = None
    active: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now()

    def deactivate(self) -> None:
        """Mark session as inactive."""
        self.active = False


class SessionRegistry:
    """Generic session management registry.

    Provides common session CRUD operations and cleanup functionality
    that can be used by both ACP agent and server implementations.

    Example usage:
        registry = SessionRegistry()
        session = registry.create("session-123", log_manager)
        session = registry.get("session-123")
        registry.remove("session-123")
        cleaned = registry.cleanup_inactive(max_age_minutes=60)
    """

    def __init__(self) -> None:
        """Initialize the session registry."""
        self._sessions: dict[str, BaseSession] = {}

    def create(
        self,
        session_id: str,
        log: LogManager | None = None,
        conversation_id: str | None = None,
    ) -> BaseSession:
        """Create a new session.

        Args:
            session_id: Unique session identifier
            log: Optional LogManager instance for direct log access (ACP pattern)
            conversation_id: Optional conversation ID for deferred loading (Server pattern)

        Returns:
            The created session

        Raises:
            ValueError: If session_id already exists
        """
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id} already exists")

        session = BaseSession(
            id=session_id,
            log=log,
            conversation_id=conversation_id,
        )
        self._sessions[session_id] = session
        logger.info(f"Created session: {session_id}")
        return session

    def get(self, session_id: str) -> BaseSession | None:
        """Get a session by ID.

        Args:
            session_id: Session identifier

        Returns:
            The session if found, None otherwise
        """
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> bool:
        """Remove a session.

        Args:
            session_id: Session identifier

        Returns:
            True if session was removed, False if not found
        """
        if session_id in self._sessions:
            session = self._sessions.pop(session_id)
            session.deactivate()
            logger.info(f"Removed session: {session_id}")
            return True
        return False

    def list_sessions(self) -> list[str]:
        """List all active session IDs.

        Returns:
            List of session IDs
        """
        return list(self._sessions.keys())

    def cleanup_inactive(self, max_age_minutes: int = 60) -> list[str]:
        """Clean up inactive sessions.

        Args:
            max_age_minutes: Maximum age in minutes before cleanup

        Returns:
            List of removed session IDs
        """
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        to_remove: list[str] = []

        for session_id, session in self._sessions.items():
            if session.last_activity < cutoff and session.active:
                to_remove.append(session_id)

        for session_id in to_remove:
            self.remove(session_id)

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} inactive sessions")

        return to_remove

    def __len__(self) -> int:
        """Return number of active sessions."""
        return len(self._sessions)

    def __contains__(self, session_id: str) -> bool:
        """Check if session exists."""
        return session_id in self._sessions
