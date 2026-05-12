"""Tests for tool_target_instructions hook.

Verifies that AGENTS.md/CLAUDE.md/GEMINI.md files are loaded when a structured
file tool touches a path under a directory that contains them, without
requiring a CWD change.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gptme.hooks.tool_target_instructions import (
    MAX_NEW_FILES,
    _extract_paths,
    _resolve_directory,
    on_tool_execute_post,
)
from gptme.logmanager import Log
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var
from gptme.util.context_dedup import _content_hash


@pytest.fixture
def empty_log() -> Log:
    return Log()


@pytest.fixture(autouse=True)
def reset_contextvars():
    """Reset _loaded_agent_files_var between tests."""
    token = _loaded_agent_files_var.set(None)
    yield
    _loaded_agent_files_var.reset(token)


def _tool_use(tool: str, *args: str, **kwargs: str) -> SimpleNamespace:
    """Build a minimal stand-in for ToolUse with the fields we need."""
    return SimpleNamespace(tool=tool, args=list(args), kwargs=dict(kwargs))


class TestExtractPaths:
    def test_extracts_first_positional_arg(self):
        tu = _tool_use("read", "/tmp/foo.txt")
        assert _extract_paths(tu) == ["/tmp/foo.txt"]

    def test_extracts_path_kwarg(self):
        tu = _tool_use("save", path="/tmp/out.md")
        assert _extract_paths(tu) == ["/tmp/out.md"]

    def test_dedups_positional_and_kwarg(self):
        tu = _tool_use("read", "/tmp/foo.txt", path="/tmp/foo.txt")
        assert _extract_paths(tu) == ["/tmp/foo.txt"]

    def test_no_args_no_kwargs_empty(self):
        tu = _tool_use("read")
        assert _extract_paths(tu) == []

    def test_ignores_non_string_args(self):
        tu = _tool_use("read")
        tu.args = [123, None]
        assert _extract_paths(tu) == []


class TestResolveDirectory:
    def test_existing_directory(self, tmp_path: Path):
        d = tmp_path / "sub"
        d.mkdir()
        assert _resolve_directory(str(d)) == d.resolve()

    def test_existing_file_returns_parent(self, tmp_path: Path):
        f = tmp_path / "x.txt"
        f.write_text("hi")
        assert _resolve_directory(str(f)) == tmp_path.resolve()

    def test_nonexistent_file_in_existing_dir_returns_parent(self, tmp_path: Path):
        target = tmp_path / "newfile.txt"
        assert _resolve_directory(str(target)) == tmp_path.resolve()

    def test_garbage_path_returns_none(self):
        # NUL is invalid in path strings on most filesystems.
        assert _resolve_directory("/\0invalid") is None

    def test_relative_path_resolved_against_cwd(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "rel"
        sub.mkdir()
        assert _resolve_directory("rel") == sub.resolve()


class TestOnToolExecutePost:
    def test_injects_agents_md_for_subdir_read(
        self, tmp_path: Path, empty_log: Log, monkeypatch
    ):
        """Reading a file in a subdirectory with AGENTS.md should inject it."""
        # The hook walks from $HOME down to the target, so put both dirs under
        # the home directory for the test.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        subdir = tmp_path / "subproject"
        subdir.mkdir()
        agents = subdir / "AGENTS.md"
        agents.write_text("# Subproject instructions\nuse uv run")

        target_file = subdir / "code.py"
        target_file.write_text("x = 1")

        tu = _tool_use("read", str(target_file))
        msgs = list(on_tool_execute_post(empty_log, None, tu))

        assert msgs, "expected at least one injected message"
        msg = msgs[0]
        assert isinstance(msg, Message)
        assert msg.role == "system"
        assert "Subproject instructions" in msg.content
        assert "<agent-instructions" in msg.content

    def test_no_reinjection_when_already_loaded(
        self, tmp_path: Path, empty_log: Log, monkeypatch
    ):
        """A second tool touching the same dir should not re-inject."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        subdir = tmp_path / "p"
        subdir.mkdir()
        agents = subdir / "AGENTS.md"
        agents.write_text("# P")

        tu1 = _tool_use("read", str(subdir / "a.py"))
        first = list(on_tool_execute_post(empty_log, None, tu1))
        assert len(first) == 1

        tu2 = _tool_use("patch", str(subdir / "b.py"))
        second = list(on_tool_execute_post(empty_log, None, tu2))
        assert second == []

    def test_dedup_by_content_hash(self, tmp_path: Path, empty_log: Log, monkeypatch):
        """Two different paths with identical AGENTS.md content -> inject once."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        for name in ("orig", "worktree"):
            d = tmp_path / name
            d.mkdir()
            (d / "AGENTS.md").write_text("# Same content")

        tu1 = _tool_use("read", str(tmp_path / "orig" / "x.py"))
        first = list(on_tool_execute_post(empty_log, None, tu1))
        assert len(first) == 1

        tu2 = _tool_use("read", str(tmp_path / "worktree" / "y.py"))
        second = list(on_tool_execute_post(empty_log, None, tu2))
        assert second == [], (
            "identical content from a different path should not be re-injected"
        )

    def test_skips_unstructured_tools(
        self, tmp_path: Path, empty_log: Log, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        subdir = tmp_path / "p"
        subdir.mkdir()
        (subdir / "AGENTS.md").write_text("# P")

        tu = _tool_use("shell", f"ls {subdir}")
        msgs = list(on_tool_execute_post(empty_log, None, tu))
        assert msgs == [], "shell payloads are out of scope for Phase 1"

    def test_no_agents_md_no_messages(
        self, tmp_path: Path, empty_log: Log, monkeypatch
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        subdir = tmp_path / "empty"
        subdir.mkdir()
        tu = _tool_use("read", str(subdir / "x.py"))
        msgs = list(on_tool_execute_post(empty_log, None, tu))
        assert msgs == []

    def test_caps_at_max_new_files(self, tmp_path: Path, empty_log: Log, monkeypatch):
        """If more than MAX_NEW_FILES files exist in the tree, cap injection."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Build a deep nested tree, each level has its own AGENTS.md with
        # different content so the dedup-by-hash short-circuit doesn't fire.
        current = tmp_path
        levels = []
        for i in range(MAX_NEW_FILES + 2):
            current = current / f"level{i}"
            current.mkdir()
            (current / "AGENTS.md").write_text(f"# Level {i} instructions")
            levels.append(current)

        tu = _tool_use("read", str(current / "target.py"))
        msgs = list(on_tool_execute_post(empty_log, None, tu))
        assert len(msgs) == MAX_NEW_FILES

    def test_dedup_against_already_injected_content(
        self, tmp_path: Path, empty_log: Log, monkeypatch
    ):
        """If the loaded-files set already contains a content hash, skip it."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        subdir = tmp_path / "p"
        subdir.mkdir()
        content = "# Already loaded"
        agents = subdir / "AGENTS.md"
        agents.write_text(content)

        # Pre-seed the loaded set with the content hash
        from gptme.hooks.tool_target_instructions import _HASH_PREFIX

        _loaded_agent_files_var.set({f"{_HASH_PREFIX}{_content_hash(content)}"})

        tu = _tool_use("read", str(subdir / "a.py"))
        msgs = list(on_tool_execute_post(empty_log, None, tu))
        assert msgs == []
