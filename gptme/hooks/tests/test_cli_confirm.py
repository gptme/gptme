"""Tests for cli_confirm hook."""

from unittest.mock import patch

import pytest

from gptme.hooks.cli_confirm import (
    _get_ext_for_tool,
    _get_lang_for_tool,
    _handle_response,
    cli_confirm_hook,
    register,
    reset_auto_confirm,
    set_auto_confirm,
)
from gptme.hooks.confirm import (
    ConfirmAction,
)
from gptme.hooks.confirm import (
    reset_auto_confirm as _reset,
)
from gptme.tools.base import ToolUse


@pytest.fixture(autouse=True)
def reset_state():
    """Reset auto-confirm state before each test."""
    _reset()
    yield
    _reset()


class TestGetLangForTool:
    """Tests for _get_lang_for_tool helper."""

    def test_python_tool(self):
        assert _get_lang_for_tool("python") == "python"

    def test_ipython_tool(self):
        assert _get_lang_for_tool("ipython") == "python"

    def test_shell_tool(self):
        assert _get_lang_for_tool("shell") == "bash"

    def test_save_tool(self):
        assert _get_lang_for_tool("save") == "text"

    def test_append_tool(self):
        assert _get_lang_for_tool("append") == "text"

    def test_patch_tool(self):
        assert _get_lang_for_tool("patch") == "diff"

    def test_unknown_tool_defaults_to_text(self):
        assert _get_lang_for_tool("browser") == "text"
        assert _get_lang_for_tool("unknown") == "text"


class TestGetExtForTool:
    """Tests for _get_ext_for_tool helper."""

    def test_save_with_python_file(self):
        tool = ToolUse(tool="save", args=["test.py"], content="print('hi')")
        assert _get_ext_for_tool(tool) == "py"

    def test_save_with_js_file(self):
        tool = ToolUse(tool="save", args=["app.js"], content="console.log()")
        assert _get_ext_for_tool(tool) == "js"

    def test_append_with_md_file(self):
        tool = ToolUse(tool="append", args=["README.md"], content="# Title")
        assert _get_ext_for_tool(tool) == "md"

    def test_patch_with_file(self):
        tool = ToolUse(tool="patch", args=["file.rs"], content="diff content")
        assert _get_ext_for_tool(tool) == "rs"

    def test_save_no_extension(self):
        tool = ToolUse(tool="save", args=["Makefile"], content="all:")
        assert _get_ext_for_tool(tool) is None

    def test_save_no_args(self):
        tool = ToolUse(tool="save", args=None, content="content")
        assert _get_ext_for_tool(tool) is None

    def test_python_tool(self):
        tool = ToolUse(tool="python", args=None, content="print('hi')")
        assert _get_ext_for_tool(tool) == "py"

    def test_ipython_tool(self):
        tool = ToolUse(tool="ipython", args=None, content="print('hi')")
        assert _get_ext_for_tool(tool) == "py"

    def test_shell_tool(self):
        tool = ToolUse(tool="shell", args=None, content="ls -la")
        assert _get_ext_for_tool(tool) == "sh"

    def test_unknown_tool(self):
        tool = ToolUse(tool="browser", args=None, content="url")
        assert _get_ext_for_tool(tool) is None


class TestHandleResponse:
    """Tests for _handle_response function."""

    def _make_tool(self, tool="shell", content="ls -la"):
        return ToolUse(tool=tool, args=None, content=content)

    def test_yes_response(self):
        result = _handle_response("y", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM

    def test_yes_full_word(self):
        result = _handle_response("yes", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM

    def test_empty_response_confirms(self):
        result = _handle_response("", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM

    def test_no_response(self):
        result = _handle_response("n", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "Declined" in (result.message or "")

    def test_no_full_word(self):
        result = _handle_response("no", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP

    def test_unknown_response_skips(self):
        result = _handle_response("xyz", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "Unknown" in (result.message or "")

    @patch("gptme.hooks.cli_confirm.copy", return_value=True)
    def test_copy_response(self, mock_copy):
        result = _handle_response("c", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "clipboard" in (result.message or "").lower()
        mock_copy.assert_called_once()

    def test_copy_when_not_copiable(self):
        # When not copiable, 'c' should be treated as unknown
        result = _handle_response("c", "content", True, False, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "Unknown" in (result.message or "")

    @patch("gptme.hooks.cli_confirm.edit_text_with_editor", return_value="edited")
    def test_edit_response_with_changes(self, mock_edit):
        result = _handle_response("e", "original", True, True, self._make_tool())
        assert result.action == ConfirmAction.EDIT
        assert result.edited_content == "edited"

    @patch("gptme.hooks.cli_confirm.edit_text_with_editor", return_value="same content")
    def test_edit_response_no_changes(self, mock_edit):
        result = _handle_response("e", "same content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "No changes" in (result.message or "")

    def test_edit_when_not_editable(self):
        # When not editable, 'e' should be treated as unknown
        result = _handle_response("e", "content", False, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "Unknown" in (result.message or "")

    def test_auto_confirm_infinite(self):
        result = _handle_response("a", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM
        # Verify auto-confirm is now set
        from gptme.hooks.confirm import is_auto_confirm_active

        assert is_auto_confirm_active()

    def test_auto_confirm_with_count(self):
        result = _handle_response("a 5", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM
        from gptme.hooks.confirm import is_auto_confirm_active

        assert is_auto_confirm_active()

    def test_auto_full_word(self):
        result = _handle_response("auto", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM

    def test_auto_with_number(self):
        result = _handle_response("auto 10", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.CONFIRM

    def test_help_response(self):
        with patch("gptme.hooks.cli_confirm.print_confirmation_help"):
            result = _handle_response("?", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP
        assert "Help" in (result.message or "")

    def test_help_h_response(self):
        with patch("gptme.hooks.cli_confirm.print_confirmation_help"):
            result = _handle_response("h", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP

    def test_help_full_word(self):
        with patch("gptme.hooks.cli_confirm.print_confirmation_help"):
            result = _handle_response("help", "content", True, True, self._make_tool())
        assert result.action == ConfirmAction.SKIP


class TestCliConfirmHook:
    """Tests for the main cli_confirm_hook function."""

    def test_auto_confirm_mode(self):
        """When auto-confirm is active, hook confirms without prompting."""
        set_auto_confirm(None)  # Infinite auto-confirm
        tool = ToolUse(tool="shell", args=None, content="ls")
        with patch("gptme.hooks.cli_confirm.print_preview"):
            result = cli_confirm_hook(tool)
        assert result.action == ConfirmAction.CONFIRM

    def test_auto_confirm_with_count(self):
        """Auto-confirm with count decrements and eventually stops."""
        set_auto_confirm(2)
        tool = ToolUse(tool="shell", args=None, content="ls")

        with patch("gptme.hooks.cli_confirm.print_preview"):
            result1 = cli_confirm_hook(tool)
            result2 = cli_confirm_hook(tool)

        assert result1.action == ConfirmAction.CONFIRM
        assert result2.action == ConfirmAction.CONFIRM

        # Third call should prompt (count exhausted)
        # We'd need to mock prompt_alert for this

    def test_preview_shown_even_in_auto_confirm(self):
        """Preview is always shown, even in auto-confirm mode."""
        set_auto_confirm(None)
        tool = ToolUse(tool="shell", args=None, content="echo hello")
        with patch("gptme.hooks.cli_confirm.print_preview") as mock_preview:
            cli_confirm_hook(tool)
        mock_preview.assert_called_once_with("echo hello", "bash", copy=True)

    def test_no_content_skips_preview(self):
        """No preview when tool has no content."""
        set_auto_confirm(None)
        tool = ToolUse(tool="shell", args=None, content=None)
        with patch("gptme.hooks.cli_confirm.print_preview") as mock_preview:
            result = cli_confirm_hook(tool)
        mock_preview.assert_not_called()
        assert result.action == ConfirmAction.CONFIRM

    @patch("gptme.hooks.cli_confirm.termios", None)
    @patch("gptme.hooks.cli_confirm.prompt_alert", return_value="y")
    @patch("gptme.hooks.cli_confirm.print_preview")
    @patch("gptme.hooks.cli_confirm.print_bell")
    def test_interactive_confirm(self, mock_bell, mock_preview, mock_prompt):
        """Interactive confirmation with user saying yes."""
        tool = ToolUse(tool="shell", args=None, content="rm -rf /tmp/test")
        result = cli_confirm_hook(tool)
        assert result.action == ConfirmAction.CONFIRM
        mock_bell.assert_called_once()

    @patch("gptme.hooks.cli_confirm.termios", None)
    @patch("gptme.hooks.cli_confirm.prompt_alert", return_value="n")
    @patch("gptme.hooks.cli_confirm.print_preview")
    @patch("gptme.hooks.cli_confirm.print_bell")
    def test_interactive_decline(self, mock_bell, mock_preview, mock_prompt):
        """Interactive confirmation with user saying no."""
        tool = ToolUse(tool="shell", args=None, content="dangerous command")
        result = cli_confirm_hook(tool)
        assert result.action == ConfirmAction.SKIP

    def test_custom_preview(self):
        """Custom preview overrides tool content."""
        set_auto_confirm(None)
        tool = ToolUse(tool="patch", args=None, content="raw patch")
        with patch("gptme.hooks.cli_confirm.print_preview") as mock_preview:
            cli_confirm_hook(tool, preview="formatted diff")
        mock_preview.assert_called_once_with("formatted diff", "diff", copy=True)


class TestRegister:
    """Tests for hook registration."""

    def test_register_adds_hook(self):
        """register() adds cli_confirm to the hook registry."""
        from gptme.hooks import HookType, get_hooks

        register()

        hooks = get_hooks(HookType.TOOL_CONFIRM)
        hook_names = [h.name for h in hooks]
        assert "cli_confirm" in hook_names


class TestAutoConfirmWrappers:
    """Tests for the re-exported auto-confirm functions."""

    def test_set_and_reset(self):
        """set_auto_confirm and reset_auto_confirm work correctly."""
        from gptme.hooks.confirm import is_auto_confirm_active

        assert not is_auto_confirm_active()

        set_auto_confirm(None)
        assert is_auto_confirm_active()

        reset_auto_confirm()
        assert not is_auto_confirm_active()

    def test_set_with_count(self):
        """set_auto_confirm with count works."""
        from gptme.hooks.confirm import is_auto_confirm_active

        set_auto_confirm(3)
        assert is_auto_confirm_active()

        reset_auto_confirm()
        assert not is_auto_confirm_active()
