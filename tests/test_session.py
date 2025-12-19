"""Tests for the shared session module."""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from gptme.session import BaseSession, SessionRegistry


@pytest.fixture
def mock_log() -> MagicMock:
    """Create a mock LogManager."""
    return MagicMock()


@pytest.fixture
def registry() -> SessionRegistry:
    """Create a fresh session registry."""
    return SessionRegistry()


class TestBaseSession:
    """Tests for BaseSession dataclass."""

    def test_create_session(self, mock_log: MagicMock) -> None:
        """Test creating a base session."""
        session = BaseSession(id="test-123", log=mock_log)

        assert session.id == "test-123"
        assert session.log is mock_log
        assert session.active is True
        assert isinstance(session.created_at, datetime)
        assert isinstance(session.last_activity, datetime)

    def test_touch_updates_activity(self, mock_log: MagicMock) -> None:
        """Test that touch() updates last_activity timestamp."""
        session = BaseSession(id="test-123", log=mock_log)
        original_time = session.last_activity

        # Small delay to ensure different timestamp
        time.sleep(0.01)
        session.touch()

        assert session.last_activity > original_time


class TestSessionRegistry:
    """Tests for SessionRegistry class."""

    def test_create_session(
        self, registry: SessionRegistry, mock_log: MagicMock
    ) -> None:
        """Test creating a session in the registry."""
        session = registry.create("session-1", mock_log)

        assert session.id == "session-1"
        assert session.log is mock_log
        assert len(registry) == 1
        assert "session-1" in registry

    def test_create_duplicate_raises(
        self, registry: SessionRegistry, mock_log: MagicMock
    ) -> None:
        """Test that creating a duplicate session raises ValueError."""
        registry.create("session-1", mock_log)

        with pytest.raises(ValueError, match="already exists"):
            registry.create("session-1", mock_log)

    def test_get_session(self, registry: SessionRegistry, mock_log: MagicMock) -> None:
        """Test getting a session by ID."""
        registry.create("session-1", mock_log)

        session = registry.get("session-1")
        assert session is not None
        assert session.id == "session-1"

    def test_get_nonexistent_returns_none(self, registry: SessionRegistry) -> None:
        """Test that getting a nonexistent session returns None."""
        session = registry.get("nonexistent")
        assert session is None

    def test_remove_session(
        self, registry: SessionRegistry, mock_log: MagicMock
    ) -> None:
        """Test removing a session."""
        registry.create("session-1", mock_log)
        assert len(registry) == 1

        result = registry.remove("session-1")

        assert result is True
        assert len(registry) == 0
        assert "session-1" not in registry

    def test_remove_nonexistent_returns_false(self, registry: SessionRegistry) -> None:
        """Test that removing a nonexistent session returns False."""
        result = registry.remove("nonexistent")
        assert result is False

    def test_list_sessions(
        self, registry: SessionRegistry, mock_log: MagicMock
    ) -> None:
        """Test listing all session IDs."""
        registry.create("session-1", mock_log)
        registry.create("session-2", mock_log)
        registry.create("session-3", mock_log)

        sessions = registry.list_sessions()

        assert set(sessions) == {"session-1", "session-2", "session-3"}

    def test_cleanup_inactive(
        self, registry: SessionRegistry, mock_log: MagicMock
    ) -> None:
        """Test cleaning up inactive sessions."""
        # Create sessions
        registry.create("old-session", mock_log)
        registry.create("new-session", mock_log)

        # Make old-session appear old
        old_session = registry.get("old-session")
        assert old_session is not None
        old_session.last_activity = datetime.now() - timedelta(minutes=120)

        # Cleanup with 60 minute threshold
        removed = registry.cleanup_inactive(max_age_minutes=60)

        assert "old-session" in removed
        assert "new-session" not in removed
        assert len(registry) == 1
        assert "new-session" in registry

    def test_len(self, registry: SessionRegistry, mock_log: MagicMock) -> None:
        """Test __len__ returns correct count."""
        assert len(registry) == 0

        registry.create("session-1", mock_log)
        assert len(registry) == 1

        registry.create("session-2", mock_log)
        assert len(registry) == 2

    def test_contains(self, registry: SessionRegistry, mock_log: MagicMock) -> None:
        """Test __contains__ membership check."""
        assert "session-1" not in registry

        registry.create("session-1", mock_log)
        assert "session-1" in registry

        registry.remove("session-1")
        assert "session-1" not in registry
