"""Tests for agents_md_inject hook.

Updated for CWD_CHANGED hook type (Issue #1521): the hook now subscribes to
CWD_CHANGED and receives (old_cwd, new_cwd) directly instead of using its
own pre/post CWD ContextVar comparison.
"""

import os
from pathlib import Path

import pytest

from gptme.hooks.agents_md_inject import (
    _HASH_PREFIX,
    _derive_loaded_files_from_log,
    _get_loaded_files,
    on_cwd_changed,
)
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var
from gptme.util.context_dedup import _content_hash


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


class TestDeriveLoadedFilesFromLog:
    """Tests for _derive_loaded_files_from_log — server-mode fallback."""

    def test_empty_log_returns_empty_set(self):
        """Empty log has no agent instructions."""
        from gptme.logmanager import Log

        assert _derive_loaded_files_from_log(Log()) == set()

    def test_parses_agent_instructions_source(self, tmp_path: Path):
        """Extracts resolved file path from <agent-instructions source="..."> tag."""
        from gptme.logmanager import Log

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("# Instructions")
        resolved = str(agents_file.resolve())
        # Use ~ path like the real injection code does
        try:
            display = "~/" + str(agents_file.resolve().relative_to(Path.home()))
        except ValueError:
            display = str(agents_file.resolve())

        msg = Message(
            "system",
            f'<agent-instructions source="{display}">\n# Instructions\n</agent-instructions>',
        )
        log = Log(messages=[msg])
        loaded = _derive_loaded_files_from_log(log)
        assert resolved in loaded

    def test_ignores_non_system_messages(self):
        """User and assistant messages don't contain agent-instructions."""
        from gptme.logmanager import Log

        msg = Message("user", '<agent-instructions source="/fake/AGENTS.md">')
        log = Log(messages=[msg])
        assert _derive_loaded_files_from_log(log) == set()

    def test_multiple_files_in_log(self, tmp_path: Path):
        """Multiple agent-instructions messages are all parsed."""
        from gptme.logmanager import Log

        f1 = tmp_path / "AGENTS.md"
        f2 = tmp_path / "subdir" / "CLAUDE.md"
        f2.parent.mkdir()
        f1.write_text("# A")
        f2.write_text("# B")

        msgs = [
            Message("system", f'<agent-instructions source="{f1}">'),
            Message("system", f'<agent-instructions source="{f2}">'),
        ]
        loaded = _derive_loaded_files_from_log(Log(messages=msgs))
        assert str(f1.resolve()) in loaded
        assert str(f2.resolve()) in loaded


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

    def test_falls_back_to_log_when_contextvar_empty(self, tmp_path: Path):
        """Server-mode fallback: when ContextVar is None, derive from log."""
        from gptme.logmanager import Log

        agents_file = tmp_path / "AGENTS.md"
        agents_file.write_text("# Instructions")
        resolved = str(agents_file.resolve())
        try:
            display = "~/" + str(agents_file.resolve().relative_to(Path.home()))
        except ValueError:
            display = str(agents_file.resolve())

        msg = Message(
            "system",
            f'<agent-instructions source="{display}">\n# Instructions\n</agent-instructions>',
        )
        log = Log(messages=[msg])

        # ContextVar is None (simulates fresh Flask request context)
        assert _loaded_agent_files_var.get() is None
        files = _get_loaded_files(log)
        assert resolved in files


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

    def test_skips_injection_when_content_already_loaded(
        self, tmp_path: Path, empty_log
    ):
        """Content-hash dedup: same content in different path must not be re-injected.

        This is the worktree scenario described in gptme/gptme#2254: cwd changes from
        ~/Programming/gptme/ to /tmp/worktrees/gptme-version-fix/ — both have the same
        AGENTS.md but different absolute paths, so path-based dedup misses the duplicate.
        """
        # Simulate original repo directory
        orig_dir = tmp_path / "original"
        orig_dir.mkdir()
        orig_file = orig_dir / "AGENTS.md"
        shared_content = "# Shared Instructions\nDo great things."
        orig_file.write_text(shared_content)

        # Simulate worktree directory with IDENTICAL content at a different path
        worktree_dir = tmp_path / "worktree"
        worktree_dir.mkdir()
        worktree_file = worktree_dir / "AGENTS.md"
        worktree_file.write_text(shared_content)

        # Simulate prompt_workspace() having loaded the original file at session start:
        # it adds both the resolved path AND the content hash to the tracking set.
        loaded = _get_loaded_files()
        loaded.add(str(orig_file.resolve()))
        loaded.add(f"{_HASH_PREFIX}{_content_hash(shared_content)}")

        original = os.getcwd()
        os.chdir(worktree_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=worktree_dir,
                    old_cwd=str(orig_dir),
                    new_cwd=str(worktree_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) == 0, (
                "Identical content from a different path should not be re-injected"
            )
        finally:
            os.chdir(original)

    def test_different_content_in_different_path_is_injected(
        self, tmp_path: Path, empty_log
    ):
        """Sanity check: different content at a new path must still be injected."""
        orig_dir = tmp_path / "original"
        orig_dir.mkdir()
        orig_file = orig_dir / "AGENTS.md"
        orig_file.write_text("# Original instructions.")

        worktree_dir = tmp_path / "worktree"
        worktree_dir.mkdir()
        worktree_file = worktree_dir / "AGENTS.md"
        worktree_file.write_text("# Different instructions — unique to worktree.")

        # Mark the original as already loaded
        loaded = _get_loaded_files()
        loaded.add(str(orig_file.resolve()))

        original = os.getcwd()
        os.chdir(worktree_dir)
        try:
            msgs = list(
                on_cwd_changed(
                    log=empty_log,
                    workspace=worktree_dir,
                    old_cwd=str(orig_dir),
                    new_cwd=str(worktree_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            assert len(agent_msgs) >= 1, "Different content should be injected"
            assert "unique to worktree" in agent_msgs[0].content
        finally:
            os.chdir(original)

    def test_server_mode_no_reinjection_on_cwd_change(self, tmp_path: Path):
        """Server-mode regression test for gptme#1958.

        When ContextVar is None (fresh Flask request context), the hook should
        derive already-loaded files from the conversation log and NOT re-inject.
        """
        from gptme.logmanager import Log

        new_dir = tmp_path / "project"
        new_dir.mkdir()
        agents_file = new_dir / "AGENTS.md"
        agents_file.write_text("# Instructions")

        # Build the display path the way the real injection code does
        try:
            display = "~/" + str(agents_file.resolve().relative_to(Path.home()))
        except ValueError:
            display = str(agents_file.resolve())

        # Simulate a log that already has this file injected (from a prior request)
        prior_injection = Message(
            "system",
            f'<agent-instructions source="{display}">\n# Instructions\n</agent-instructions>',
        )
        log_with_prior = Log(messages=[prior_injection])

        original = os.getcwd()
        os.chdir(new_dir)
        try:
            # ContextVar is None — simulates a fresh Flask request context
            assert _loaded_agent_files_var.get() is None

            msgs = list(
                on_cwd_changed(
                    log=log_with_prior,
                    workspace=new_dir,
                    old_cwd=original,
                    new_cwd=str(new_dir),
                    tool_use=None,
                )
            )
            agent_msgs = [m for m in msgs if isinstance(m, Message)]
            # Should NOT re-inject — file is already in the log
            assert len(agent_msgs) == 0, (
                "AGENTS.md should not be re-injected when already present in log"
            )
        finally:
            os.chdir(original)
