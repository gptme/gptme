"""Tests for agents_md_inject hook."""

import os
from pathlib import Path

import pytest

from gptme.hooks.agents_md_inject import (
    _cwd_before_var,
    _get_loaded_files,
    post_execute,
    pre_execute,
)
from gptme.logmanager import Log
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def empty_log() -> Log:
    """Create an empty Log for testing."""
    return Log()


@pytest.fixture(autouse=True)
def reset_contextvars():
    """Reset ContextVars between tests."""
    cwd_token = _cwd_before_var.set(None)
    loaded_token = _loaded_agent_files_var.set(None)
    yield
    _cwd_before_var.reset(cwd_token)
    _loaded_agent_files_var.reset(loaded_token)


class TestGetLoadedFiles:
    """Tests for _get_loaded_files helper."""

    def test_initializes_empty_set(self):
        """When no files have been loaded, returns empty set."""
        files = _get_loaded_files()
        assert isinstance(files, set)
        assert len(files) == 0

    def test_returns_existing_set(self):
        """When files have been set, returns them."""
        existing = {"/path/to/AGENTS.md", "/path/to/CLAUDE.md"}
        _loaded_agent_files_var.set(existing)
        files = _get_loaded_files()
        assert files == existing

    def test_mutations_persist(self):
        """Adding to the returned set persists across calls."""
        files = _get_loaded_files()
        files.add("/new/file.md")
        assert "/new/file.md" in _get_loaded_files()


class TestPreExecute:
    """Tests for pre_execute hook."""

    def test_stores_current_cwd(self, workspace: Path, empty_log: Log):
        cwd = os.getcwd()
        list(pre_execute(log=empty_log, workspace=workspace, tool_use=None))
        assert _cwd_before_var.get() == cwd

    def test_yields_no_messages(self, workspace: Path, empty_log: Log):
        msgs = list(pre_execute(log=empty_log, workspace=workspace, tool_use=None))
        assert len(msgs) == 0


class TestPostExecute:
    """Tests for post_execute hook."""

    def test_no_action_when_cwd_unchanged(self, workspace: Path, empty_log: Log):
        """No messages when CWD hasn't changed."""
        _cwd_before_var.set(os.getcwd())
        msgs = list(post_execute(log=empty_log, workspace=workspace, tool_use=None))
        assert len(msgs) == 0

    def test_no_action_when_no_stored_cwd(self, workspace: Path, empty_log: Log):
        """No messages when pre_execute wasn't called (no stored CWD)."""
        msgs = list(post_execute(log=empty_log, workspace=workspace, tool_use=None))
        assert len(msgs) == 0

    def test_injects_agents_md_on_cwd_change(self, tmp_path: Path, empty_log: Log):
        """When CWD changes to a dir with AGENTS.md, inject its content."""
        # Set up directory with an AGENTS.md
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# My Agent Instructions\nDo good things.")

        # Store original CWD
        original = os.getcwd()
        _cwd_before_var.set(original)

        # Change to new directory
        os.chdir(new_dir)
        try:
            msgs = list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            # Should find and inject the AGENTS.md
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            # Check that the content was injected
            injected = agent_msgs[0].content
            assert "My Agent Instructions" in injected
            assert "Do good things" in injected
            assert "agent-instructions" in injected
        finally:
            os.chdir(original)

    def test_skips_already_loaded_files(self, tmp_path: Path, empty_log: Log):
        """Files already in the loaded set should not be re-injected."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        # Mark as already loaded
        loaded = _get_loaded_files()
        loaded.add(str(agents_file.resolve()))

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            msgs = list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) == 0
        finally:
            os.chdir(original)

    def test_newly_loaded_files_added_to_set(self, tmp_path: Path, empty_log: Log):
        """After injection, the file should be in the loaded set."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            loaded = _get_loaded_files()
            assert str(agents_file.resolve()) in loaded
        finally:
            os.chdir(original)

    def test_claude_md_also_detected(self, tmp_path: Path, empty_log: Log):
        """CLAUDE.md files should also be detected and injected."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        claude_file = new_dir / "CLAUDE.md"
        claude_file.write_text("# Claude instructions")

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            msgs = list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            assert "Claude instructions" in agent_msgs[0].content
        finally:
            os.chdir(original)

    def test_no_injection_when_no_agent_files(self, tmp_path: Path, empty_log: Log):
        """No messages when CWD changes to a dir without agent files."""
        new_dir = tmp_path / "empty_project"
        new_dir.mkdir()
        # Create a regular file but no AGENTS.md
        (new_dir / "README.md").write_text("# Readme")

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            msgs = list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) == 0
        finally:
            os.chdir(original)

    def test_display_path_in_injected_message(self, tmp_path: Path, empty_log: Log):
        """Injected messages should include a display path."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        original = os.getcwd()
        _cwd_before_var.set(original)

        os.chdir(new_dir)
        try:
            msgs = list(post_execute(log=empty_log, workspace=new_dir, tool_use=None))
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            # Should include source attribute with path
            assert "source=" in agent_msgs[0].content
        finally:
            os.chdir(original)
