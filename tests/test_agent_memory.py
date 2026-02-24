"""Tests for persistent agent memory system."""

import os
from unittest.mock import patch

import pytest

from gptme.dirs import get_agent_memory_dir
from gptme.tools.subagent import (
    _build_memory_system_message,
    _load_agent_memory,
)


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Set up a temporary data directory for testing."""
    data_dir = tmp_path / "gptme-data"
    data_dir.mkdir()
    with patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp_path)}):
        yield data_dir


class TestGetAgentMemoryDir:
    """Tests for get_agent_memory_dir."""

    def test_creates_directory(self, tmp_data_dir):
        """Memory directory is created if it doesn't exist."""
        memory_dir = get_agent_memory_dir("explorer")
        assert memory_dir.exists()
        assert memory_dir.is_dir()
        assert memory_dir.name == "explorer"

    def test_returns_correct_path(self, tmp_data_dir):
        """Memory directory is under agent-memories/."""
        memory_dir = get_agent_memory_dir("researcher")
        assert "agent-memories" in str(memory_dir)
        assert memory_dir.name == "researcher"

    def test_idempotent(self, tmp_data_dir):
        """Calling twice returns same path without error."""
        dir1 = get_agent_memory_dir("explorer")
        dir2 = get_agent_memory_dir("explorer")
        assert dir1 == dir2

    def test_different_profiles_different_dirs(self, tmp_data_dir):
        """Each profile gets its own directory."""
        dir1 = get_agent_memory_dir("explorer")
        dir2 = get_agent_memory_dir("researcher")
        assert dir1 != dir2
        assert dir1.name == "explorer"
        assert dir2.name == "researcher"


class TestLoadAgentMemory:
    """Tests for _load_agent_memory."""

    def test_no_profile_returns_none(self):
        """No profile name returns (None, None)."""
        content, memory_dir = _load_agent_memory(None)
        assert content is None
        assert memory_dir is None

    def test_empty_memory_returns_none_content(self, tmp_data_dir):
        """Profile with no MEMORY.md returns None content but valid dir."""
        content, memory_dir = _load_agent_memory("explorer")
        assert content is None
        assert memory_dir is not None
        assert memory_dir.exists()

    def test_loads_existing_memory(self, tmp_data_dir):
        """Existing MEMORY.md content is loaded."""
        memory_dir = get_agent_memory_dir("explorer")
        memory_file = memory_dir / "MEMORY.md"
        memory_file.write_text(
            "# Explorer Memory\n\n- Pattern: always check tests first\n"
        )

        content, returned_dir = _load_agent_memory("explorer")
        assert content is not None
        assert "Pattern: always check tests first" in content
        assert returned_dir == memory_dir

    def test_empty_file_returns_none_content(self, tmp_data_dir):
        """Empty MEMORY.md returns None content."""
        memory_dir = get_agent_memory_dir("explorer")
        memory_file = memory_dir / "MEMORY.md"
        memory_file.write_text("")

        content, returned_dir = _load_agent_memory("explorer")
        assert content is None
        assert returned_dir is not None

    def test_whitespace_only_returns_none_content(self, tmp_data_dir):
        """Whitespace-only MEMORY.md returns None content."""
        memory_dir = get_agent_memory_dir("explorer")
        memory_file = memory_dir / "MEMORY.md"
        memory_file.write_text("   \n\n  \n")

        content, returned_dir = _load_agent_memory("explorer")
        assert content is None
        assert returned_dir is not None


class TestBuildMemorySystemMessage:
    """Tests for _build_memory_system_message."""

    def test_with_memory_content(self, tmp_path):
        """System message includes memory content."""
        memory_dir = tmp_path / "memories" / "explorer"
        memory_dir.mkdir(parents=True)
        msg = _build_memory_system_message("- Key pattern found", memory_dir)
        assert msg.role == "system"
        assert "Agent Memory" in msg.content
        assert "Key pattern found" in msg.content
        assert str(memory_dir) in msg.content

    def test_without_memory_content(self, tmp_path):
        """System message handles empty memory."""
        memory_dir = tmp_path / "memories" / "explorer"
        memory_dir.mkdir(parents=True)
        msg = _build_memory_system_message(None, memory_dir)
        assert msg.role == "system"
        assert "currently empty" in msg.content
        assert str(memory_dir) in msg.content

    def test_includes_save_instructions(self, tmp_path):
        """System message tells agent how to save memories."""
        memory_dir = tmp_path / "memories" / "explorer"
        memory_dir.mkdir(parents=True)
        msg = _build_memory_system_message(None, memory_dir)
        assert "MEMORY.md" in msg.content
        assert "persist across sessions" in msg.content
