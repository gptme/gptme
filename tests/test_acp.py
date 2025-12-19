"""Tests for ACP (Agent Client Protocol) support.

Tests Phase 3 features: session persistence and cancellation.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if acp is not installed
pytest.importorskip("acp", reason="agent-client-protocol not installed")

from gptme.acp.agent import (
    ACP_SESSIONS_DIR,
    GptmeAgent,
    SessionCancelled,
)


@pytest.fixture
def agent() -> GptmeAgent:
    """Create a GptmeAgent instance for testing."""
    return GptmeAgent()


class TestSessionPersistence:
    """Tests for session persistence functionality."""

    def test_session_dir_creation(self, agent: GptmeAgent):
        """Test that session directory path is correctly computed."""
        session_id = "test_session_123"
        session_dir = agent._get_session_dir(session_id)

        assert session_dir == ACP_SESSIONS_DIR / session_id
        assert isinstance(session_dir, Path)

    def test_save_and_load_metadata(self, agent: GptmeAgent, tmp_path: Path):
        """Test saving and loading session metadata."""
        session_id = "test_metadata_session"

        # Patch ACP_SESSIONS_DIR to use temp directory
        with patch("gptme.acp.agent.ACP_SESSIONS_DIR", tmp_path):
            # Set up metadata
            agent._session_metadata[session_id] = {
                "cwd": "/test/path",
                "model": "test-model",
                "mcp_servers": [],
            }

            # Save metadata
            agent._save_session_metadata(session_id)

            # Verify file was created
            metadata_file = tmp_path / session_id / "metadata.json"
            assert metadata_file.exists()

            # Load metadata back
            loaded = agent._load_session_metadata(session_id)
            assert loaded is not None
            assert loaded["cwd"] == "/test/path"
            assert loaded["model"] == "test-model"

    def test_load_nonexistent_metadata(self, agent: GptmeAgent, tmp_path: Path):
        """Test loading metadata for nonexistent session."""
        with patch("gptme.acp.agent.ACP_SESSIONS_DIR", tmp_path):
            loaded = agent._load_session_metadata("nonexistent_session")
            assert loaded is None

    def test_list_persistent_sessions(self, agent: GptmeAgent, tmp_path: Path):
        """Test listing persistent sessions."""
        with patch("gptme.acp.agent.ACP_SESSIONS_DIR", tmp_path):
            # Create some test sessions
            for session_id in ["session1", "session2", "session3"]:
                session_dir = tmp_path / session_id
                session_dir.mkdir()
                (session_dir / "metadata.json").write_text('{"cwd": "/test"}')

            # Create a directory without metadata (should be excluded)
            (tmp_path / "incomplete_session").mkdir()

            sessions = agent._list_persistent_sessions()
            assert len(sessions) == 3
            assert "session1" in sessions
            assert "session2" in sessions
            assert "session3" in sessions
            assert "incomplete_session" not in sessions


class TestCancellation:
    """Tests for session cancellation functionality."""

    def test_cancel_flag_set(self, agent: GptmeAgent):
        """Test that cancel sets the cancellation flag."""
        session_id = "test_cancel_session"

        # Set up a session
        agent._sessions[session_id] = MagicMock()
        agent._tool_calls[session_id] = {}

        # Run cancel (async)
        import asyncio

        async def run_cancel():
            await agent.cancel(session_id)

        asyncio.run(run_cancel())

        assert agent._cancel_requested.get(session_id) is True

    def test_cancel_unknown_session(self, agent: GptmeAgent):
        """Test that cancelling unknown session doesn't raise."""
        import asyncio

        async def run_cancel():
            # Should not raise
            await agent.cancel("unknown_session_id")

        asyncio.run(run_cancel())
        # Verify no flag was set
        assert "unknown_session_id" not in agent._cancel_requested

    def test_session_cancelled_exception(self):
        """Test SessionCancelled exception."""
        with pytest.raises(SessionCancelled):
            raise SessionCancelled("Test cancellation")


class TestAgentInitialization:
    """Tests for agent initialization."""


class TestLoadSessionIntegration:
    """Integration tests for load_session async method."""

    def test_load_session_already_in_memory(self, agent: GptmeAgent):
        """Test loading a session that's already in memory."""
        import asyncio

        session_id = "already_loaded_session"

        # Put session in memory
        agent._sessions[session_id] = MagicMock()

        async def run_test():
            return await agent.load_session(session_id)

        result = asyncio.run(run_test())

        assert result["session_id"] == session_id
        assert result["status"] == "loaded"

    def test_load_session_not_found(self, agent: GptmeAgent, tmp_path: Path):
        """Test loading a session that doesn't exist."""
        import asyncio

        with patch("gptme.acp.agent.ACP_SESSIONS_DIR", tmp_path):

            async def run_test():
                return await agent.load_session("nonexistent_session")

            with pytest.raises(ValueError, match="not found"):
                asyncio.run(run_test())

    def test_load_session_from_disk(self, agent: GptmeAgent, tmp_path: Path):
        """Test loading a session from persistent storage."""
        import asyncio
        import json

        session_id = "persisted_session"

        with patch("gptme.acp.agent.ACP_SESSIONS_DIR", tmp_path):
            # Create session directory structure
            session_dir = tmp_path / session_id
            session_dir.mkdir()

            # Create metadata
            metadata = {"cwd": "/test/cwd", "model": "test-model", "mcp_servers": []}
            (session_dir / "metadata.json").write_text(json.dumps(metadata))

            # Create log directory with minimal log file
            log_dir = session_dir / "log"
            log_dir.mkdir()

            # Create empty conversation.jsonl file (LogManager needs this)
            (log_dir / "conversation.jsonl").write_text("")

            async def run_test():
                return await agent.load_session(session_id)

            # Mock init to avoid full gptme initialization
            with patch.object(agent, "_initialized", True):
                result = asyncio.run(run_test())

            assert result["session_id"] == session_id
            assert result["status"] == "loaded"
            assert result["cwd"] == "/test/cwd"
            assert session_id in agent._sessions
            assert session_id in agent._session_metadata
