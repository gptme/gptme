"""Tests for agents_md_inject hook.

Updated for CWD_CHANGED hook type (Issue #1521): the hook now subscribes to
CWD_CHANGED and receives (old_cwd, new_cwd) directly instead of using its
own pre/post CWD ContextVar comparison.
"""

import os
from pathlib import Path

import pytest

from gptme.hooks.agents_md_inject import (
    _get_loaded_files,
    on_cwd_changed,
)
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def empty_log():
    from gptme.logmanager import Log

    return Log()


@pytest.fixture(autouse=True)
def reset_contextvars():
    """Reset ContextVars between tests."""
    loaded_token = _loaded_agent_files_var.set(None)
    yield
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


class TestOnCwdChanged:
    """Tests for on_cwd_changed hook."""

    def test_injects_agents_md_on_cwd_change(self, tmp_path: Path, empty_log):
        """When CWD changes to a dir with AGENTS.md, inject its content."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# My Agent Instructions\nDo good things.")

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            injected = agent_msgs[0].content
            assert "My Agent Instructions" in injected
            assert "Do good things" in injected
            assert "agent-instructions" in injected
        finally:
            os.chdir(original)

    def test_skips_already_loaded_files(self, tmp_path: Path, empty_log):
        """Files already in the loaded set should not be re-injected."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        # Mark as already loaded
        loaded = _get_loaded_files()
        loaded.add(str(agents_file.resolve()))

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) == 0
        finally:
            os.chdir(original)

    def test_newly_loaded_files_added_to_set(self, tmp_path: Path, empty_log):
        """After injection, the file should be in the loaded set."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            loaded = _get_loaded_files()
            assert str(agents_file.resolve()) in loaded
        finally:
            os.chdir(original)

    def test_claude_md_also_detected(self, tmp_path: Path, empty_log):
        """CLAUDE.md files should also be detected and injected."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        claude_file = new_dir / "CLAUDE.md"
        claude_file.write_text("# Claude instructions")

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            assert "Claude instructions" in agent_msgs[0].content
        finally:
            os.chdir(original)

    def test_no_injection_when_no_agent_files(self, tmp_path: Path, empty_log):
        """No messages when CWD changes to a dir without agent files."""
        new_dir = tmp_path / "empty_project"
        new_dir.mkdir()
        (new_dir / "README.md").write_text("# Readme")

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [
                m for m in msgs if isinstance(m, Message) and str(tmp_path) in m.content
            ]
            assert len(agent_msgs) == 0
        finally:
            os.chdir(original)

    def test_display_path_in_injected_message(self, tmp_path: Path, empty_log):
        """Injected messages should include a display path."""
        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1
            assert "source=" in agent_msgs[0].content
        finally:
            os.chdir(original)
