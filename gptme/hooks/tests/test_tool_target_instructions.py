"""Tests for the tool_target_instructions hook.

Phase 1: tool.execute.post path discovery for structured file tools.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gptme.hooks.tool_target_instructions import (
    _extract_tool_paths,
    on_tool_execute_post,
)
from gptme.message import Message
from gptme.prompts import _loaded_agent_files_var
from gptme.tools.base import ToolUse


@pytest.fixture(autouse=True)
def _reset_loaded_agent_files() -> None:
    """Reset the shared ContextVar before each test to avoid cross-test leaks."""
    _loaded_agent_files_var.set(None)


def _make_agents_md(parent: Path, content: str | None = None) -> Path:
    """Helper: create an AGENTS.md in the given directory."""
    if content is None:
        content = "# Agent instructions for test\n\nDo not modify.\n"
    p = parent / "AGENTS.md"
    p.write_text(content)
    return p


class TestExtractPathsFromToolUse:
    """Tests for _extract_tool_paths — path extraction from tool kwargs."""

    def test_extracts_path_from_save(self) -> None:
        """Should extract the path kwarg from a save tool use."""
        tu = ToolUse(
            "save", ["./subdir/foo.py"], "print('hi')", {"path": "./subdir/foo.py"}
        )
        paths = _extract_tool_paths(tu)
        assert len(paths) == 1
        assert paths[0].name == "foo.py"

    def test_extracts_path_from_patch(self) -> None:
        """Should extract the path kwarg from a patch tool use."""
        tu = ToolUse(
            "patch", ["./subdir/file.py"], "...patch...", {"path": "./subdir/file.py"}
        )
        paths = _extract_tool_paths(tu)
        assert len(paths) == 1
        assert paths[0].name == "file.py"

    def test_extracts_path_from_read(self) -> None:
        """Should extract the path kwarg from a read tool use."""
        tu = ToolUse("read", ["./subdir/baz.py"], None, {"path": "./subdir/baz.py"})
        paths = _extract_tool_paths(tu)
        assert len(paths) == 1
        assert paths[0].name == "baz.py"

    def test_extracts_path_from_append(self) -> None:
        """Should extract the path kwarg from an append tool use."""
        tu = ToolUse(
            "append", ["./subdir/extra.py"], "new line", {"path": "./subdir/extra.py"}
        )
        paths = _extract_tool_paths(tu)
        assert len(paths) == 1
        assert paths[0].name == "extra.py"

    def test_shell_returns_nothing(self) -> None:
        """Shell tool kwargs ('command') are not path-like, should return nothing."""
        tu = ToolUse("shell", ["echo hello"], None, {"command": "echo hello"})
        paths = _extract_tool_paths(tu)
        assert len(paths) == 0

    def test_no_kwargs_returns_nothing(self) -> None:
        """Tool use without kwargs should return nothing."""
        tu = ToolUse("shell", ["echo hello"], None, {})
        paths = _extract_tool_paths(tu)
        assert len(paths) == 0


class TestOnToolExecutePost:
    """Tests for on_tool_execute_post — the main hook handler."""

    def test_injects_instructions_when_file_touched_in_subdir(
        self, tmp_path: Path
    ) -> None:
        """When a save tool writes to a subdir with AGENTS.md, inject instructions."""
        subdir = tmp_path / "my-project"
        subdir.mkdir()
        _make_agents_md(subdir)
        target_file = subdir / "main.py"
        target_file.write_text("# placeholder")

        log = MagicMock()
        log.messages = []

        tu = ToolUse(
            "save", [str(target_file)], "print('hi')", {"path": str(target_file)}
        )
        msgs = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) >= 1
        assert "<agent-instructions" in messages[0].content

    def test_no_reinjection_on_repeated_touch(self, tmp_path: Path) -> None:
        """Touching the same subdir twice should not re-inject instructions."""
        subdir = tmp_path / "my-project"
        subdir.mkdir()
        _make_agents_md(subdir)
        target_file = subdir / "main.py"
        target_file.write_text("# placeholder")

        log = MagicMock()
        log.messages = []

        tu = ToolUse(
            "save", [str(target_file)], "print('hi')", {"path": str(target_file)}
        )
        # First touch: should inject
        msgs1 = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages1 = [m for m in msgs1 if isinstance(m, Message)]
        assert len(messages1) == 1
        log.messages.append(messages1[0])

        # Second touch: should NOT inject
        msgs2 = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages2 = [m for m in msgs2 if isinstance(m, Message)]
        assert len(messages2) == 0

    def test_no_injection_when_no_agents_file(self, tmp_path: Path) -> None:
        """No instruction file means no injection."""
        subdir = tmp_path / "bare-dir"
        subdir.mkdir()
        target_file = subdir / "main.py"
        target_file.write_text("# bare")

        log = MagicMock()
        log.messages = []

        tu = ToolUse(
            "save", [str(target_file)], "print('hi')", {"path": str(target_file)}
        )
        msgs = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) == 0

    def test_identical_content_dedup_across_worktrees(self, tmp_path: Path) -> None:
        """Same AGENTS.md content at different paths should not re-inject."""
        subdir_a = tmp_path / "repo-a"
        subdir_b = tmp_path / "repo-b"
        subdir_a.mkdir()
        subdir_b.mkdir()

        content = "# Agent instructions for test\n\nDo not delete.\n"
        (subdir_a / "AGENTS.md").write_text(content)
        (subdir_b / "AGENTS.md").write_text(content)

        file_a = subdir_a / "main.py"
        file_b = subdir_b / "main.py"
        file_a.write_text("# a")
        file_b.write_text("# b")

        log = MagicMock()
        log.messages = []

        # First touch (repo-a)
        tu_a = ToolUse("save", [str(file_a)], "print('a')", {"path": str(file_a)})
        msgs_a = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu_a))
        messages_a = [m for m in msgs_a if isinstance(m, Message)]
        assert len(messages_a) == 1
        log.messages.append(messages_a[0])

        # Second touch (repo-b), same content, different path
        tu_b = ToolUse("save", [str(file_b)], "print('b')", {"path": str(file_b)})
        msgs_b = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu_b))
        messages_b = [m for m in msgs_b if isinstance(m, Message)]
        assert len(messages_b) == 0

    def test_shell_tool_no_injection(self, tmp_path: Path) -> None:
        """Shell tool kwargs are not path-like, so no injection should occur."""
        subdir = tmp_path / "my-project"
        subdir.mkdir()
        _make_agents_md(subdir)

        log = MagicMock()
        log.messages = []

        tu = ToolUse("shell", ["echo hello"], None, {"command": "echo hello"})
        msgs = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) == 0

    def test_read_tool_in_subdir_injects(self, tmp_path: Path) -> None:
        """Read tool touching a subdir with AGENTS.md should trigger injection."""
        subdir = tmp_path / "my-project"
        subdir.mkdir()
        _make_agents_md(subdir)
        target_file = subdir / "README.md"
        target_file.write_text("# Readme")

        log = MagicMock()
        log.messages = []

        tu = ToolUse("read", [str(target_file)], None, {"path": str(target_file)})
        msgs = list(on_tool_execute_post(log=log, workspace=tmp_path, tool_use=tu))
        messages = [m for m in msgs if isinstance(m, Message)]
        assert len(messages) >= 1
        assert "<agent-instructions" in messages[0].content


class TestRegister:
    """Tests for register() — registering TOOL_EXECUTE_POST hook."""

    def test_register_adds_hook(self) -> None:
        """register() should add a TOOL_EXECUTE_POST hook."""
        from gptme.hooks import (
            HookRegistry,
            HookType,
            get_hooks,
            get_registry,
            set_registry,
        )
        from gptme.hooks.tool_target_instructions import register

        old = get_registry()
        set_registry(HookRegistry())
        try:
            register()
            hooks = get_hooks(HookType.TOOL_EXECUTE_POST)
            names = [h.name for h in hooks]
            assert "tool_target_instructions.on_tool_execute_post" in names
        finally:
            set_registry(old)
