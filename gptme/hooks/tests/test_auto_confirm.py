"""Tests for auto_confirm hook."""

from pathlib import Path

from gptme.hooks.auto_confirm import auto_confirm_hook
from gptme.hooks.confirm import ConfirmAction
from gptme.tools.base import ToolUse


def _make_tool_use(tool: str, content: str = "") -> ToolUse:
    """Create a ToolUse for testing."""
    return ToolUse(tool=tool, args=[], content=content)


class TestAutoConfirmHook:
    """Tests for auto_confirm_hook."""

    def test_confirms_shell(self) -> None:
        result = auto_confirm_hook(_make_tool_use("shell", "ls -la"))
        assert result.action == ConfirmAction.CONFIRM

    def test_confirms_python(self) -> None:
        result = auto_confirm_hook(_make_tool_use("python", "print('hello')"))
        assert result.action == ConfirmAction.CONFIRM

    def test_confirms_save(self) -> None:
        result = auto_confirm_hook(_make_tool_use("save", "file content"))
        assert result.action == ConfirmAction.CONFIRM

    def test_confirms_with_workspace(self) -> None:
        result = auto_confirm_hook(
            _make_tool_use("shell", "make test"), workspace=Path("/tmp/test")
        )
        assert result.action == ConfirmAction.CONFIRM

    def test_confirms_with_preview(self) -> None:
        result = auto_confirm_hook(
            _make_tool_use("patch", "diff content"), preview="preview text"
        )
        assert result.action == ConfirmAction.CONFIRM

    def test_always_confirms(self) -> None:
        """auto_confirm should confirm ANY tool, regardless of content."""
        for tool_name in ["shell", "python", "save", "patch", "browser", "unknown"]:
            result = auto_confirm_hook(_make_tool_use(tool_name, "rm -rf /"))
            assert result.action == ConfirmAction.CONFIRM, (
                f"Failed for tool: {tool_name}"
            )
