"""Tests for the tool confirmation hook system."""

import pytest

from gptme.hooks import HookType, get_hooks, register_hook, unregister_hook
from gptme.hooks.confirm import (
    ConfirmAction,
    ConfirmationResult,
    get_confirmation,
)
from gptme.tools.base import ToolUse


@pytest.fixture(autouse=True)
def cleanup_hooks():
    """Clean up confirmation hooks after each test."""
    yield
    # Unregister any confirmation hooks that were registered
    for hook in get_hooks(HookType.TOOL_CONFIRM):
        unregister_hook(hook.name, HookType.TOOL_CONFIRM)


class TestConfirmationResult:
    """Tests for ConfirmationResult dataclass."""

    def test_confirm_factory(self):
        """Test confirm() factory method."""
        result = ConfirmationResult.confirm()
        assert result.action == ConfirmAction.CONFIRM
        assert result.edited_content is None
        assert result.message is None

    def test_skip_factory(self):
        """Test skip() factory method."""
        result = ConfirmationResult.skip()
        assert result.action == ConfirmAction.SKIP
        assert result.message == "Operation skipped"

    def test_skip_with_message(self):
        """Test skip() with custom message."""
        result = ConfirmationResult.skip("Custom reason")
        assert result.action == ConfirmAction.SKIP
        assert result.message == "Custom reason"

    def test_edit_factory(self):
        """Test edit() factory method."""
        result = ConfirmationResult.edit("edited content")
        assert result.action == ConfirmAction.EDIT
        assert result.edited_content == "edited content"


class TestGetConfirmation:
    """Tests for get_confirmation function."""

    def test_no_hook_auto_confirm(self):
        """Test auto-confirm when no hook is registered."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )
        result = get_confirmation(tool_use, default_confirm=True)
        assert result.action == ConfirmAction.CONFIRM

    def test_no_hook_auto_skip(self):
        """Test auto-skip when no hook is registered and default_confirm=False."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )
        result = get_confirmation(tool_use, default_confirm=False)
        assert result.action == ConfirmAction.SKIP

    def test_with_confirm_hook(self):
        """Test confirmation with a registered hook."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )

        def test_hook(tool_use, preview, workspace):
            return ConfirmationResult.confirm()

        register_hook(
            name="test_confirm",
            hook_type=HookType.TOOL_CONFIRM,
            func=test_hook,
        )

        result = get_confirmation(tool_use)
        assert result.action == ConfirmAction.CONFIRM

    def test_with_skip_hook(self):
        """Test skip result from hook."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )

        def test_hook(tool_use, preview, workspace):
            return ConfirmationResult.skip("Test skip")

        register_hook(
            name="test_skip",
            hook_type=HookType.TOOL_CONFIRM,
            func=test_hook,
        )

        result = get_confirmation(tool_use)
        assert result.action == ConfirmAction.SKIP
        assert result.message == "Test skip"

    def test_with_edit_hook(self):
        """Test edit result from hook."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="original content",
        )

        def test_hook(tool_use, preview, workspace):
            return ConfirmationResult.edit("modified content")

        register_hook(
            name="test_edit",
            hook_type=HookType.TOOL_CONFIRM,
            func=test_hook,
        )

        result = get_confirmation(tool_use)
        assert result.action == ConfirmAction.EDIT
        assert result.edited_content == "modified content"

    def test_bool_return_compatibility(self):
        """Test backward compatibility with boolean return."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )

        def bool_hook(tool_use, preview, workspace):
            return True

        register_hook(
            name="bool_hook",
            hook_type=HookType.TOOL_CONFIRM,
            func=bool_hook,
        )

        result = get_confirmation(tool_use)
        assert result.action == ConfirmAction.CONFIRM

    def test_bool_false_return(self):
        """Test boolean False return becomes skip."""
        tool_use = ToolUse(
            tool="test",
            args=[],
            kwargs={},
            content="test content",
        )

        def bool_hook(tool_use, preview, workspace):
            return False

        register_hook(
            name="bool_hook_false",
            hook_type=HookType.TOOL_CONFIRM,
            func=bool_hook,
        )

        result = get_confirmation(tool_use)
        assert result.action == ConfirmAction.SKIP


class TestAutoConfirmHook:
    """Tests for auto_confirm hook."""

    def test_auto_confirm_hook(self):
        """Test auto_confirm hook always confirms."""
        from gptme.hooks.auto_confirm import auto_confirm_hook

        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="echo hello",
        )

        result = auto_confirm_hook(tool_use, None, None)
        assert result.action == ConfirmAction.CONFIRM

    def test_auto_confirm_registration(self):
        """Test auto_confirm hook registration."""
        from gptme.hooks.auto_confirm import register

        register()

        hooks = get_hooks(HookType.TOOL_CONFIRM)
        assert any(h.name == "auto_confirm" for h in hooks)
