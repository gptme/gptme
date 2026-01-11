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


class TestServerConfirmHook:
    """Tests for server confirmation hook."""

    def test_server_confirm_pending_registration(self):
        """Test that pending confirmations can be registered and resolved."""
        from gptme.hooks.confirm import ConfirmationResult
        from gptme.hooks.server_confirm import (
            get_pending,
            register_pending,
            remove_pending,
            resolve_pending,
        )

        # Create a mock tool use
        from gptme.tools.base import ToolUse

        tooluse = ToolUse("shell", ["echo", "test"], "echo test")

        # Register a pending confirmation
        tool_id = "test-tool-123"
        pending = register_pending(tool_id, tooluse, "preview content")

        assert pending is not None
        assert pending.tool_use == tooluse
        assert pending.preview == "preview content"
        assert pending.result is None
        assert not pending.event.is_set()

        # Verify we can retrieve it
        retrieved = get_pending(tool_id)
        assert retrieved == pending

        # Resolve the pending confirmation
        result = ConfirmationResult.confirm()
        success = resolve_pending(tool_id, result)
        assert success

        # Event should be set
        assert pending.event.is_set()
        assert pending.result == result

        # Cleanup
        remove_pending(tool_id)
        assert get_pending(tool_id) is None

    def test_resolve_nonexistent_pending(self):
        """Test resolving a non-existent pending confirmation returns False."""
        from gptme.hooks.confirm import ConfirmationResult
        from gptme.hooks.server_confirm import resolve_pending

        success = resolve_pending("nonexistent-id", ConfirmationResult.confirm())
        assert not success

    def test_server_confirm_registration(self):
        """Test that server_confirm hook can be registered."""
        from gptme.hooks import HookType, clear_hooks, get_hooks
        from gptme.hooks.server_confirm import register, unregister

        # Clear any existing hooks
        clear_hooks(HookType.TOOL_CONFIRM)

        # Register server confirm hook
        register()

        # Verify registration
        hooks = get_hooks(HookType.TOOL_CONFIRM)
        assert len(hooks) == 1
        assert hooks[0].name == "server_confirm"

        # Cleanup
        unregister()
        assert len(get_hooks(HookType.TOOL_CONFIRM)) == 0

    def test_context_vars_default_none(self):
        """Test that context vars default to None."""
        from gptme.hooks.server_confirm import (
            current_conversation_id,
            current_session_id,
        )

        # In a new context, vars should be None
        assert current_conversation_id.get() is None
        assert current_session_id.get() is None

    def test_context_vars_can_be_set(self):
        """Test that context vars can be set and retrieved."""
        from gptme.hooks.server_confirm import (
            current_conversation_id,
            current_session_id,
        )

        # Set values
        token1 = current_conversation_id.set("test-conversation")
        token2 = current_session_id.set("test-session")

        try:
            assert current_conversation_id.get() == "test-conversation"
            assert current_session_id.get() == "test-session"
        finally:
            # Reset to avoid affecting other tests
            current_conversation_id.reset(token1)
            current_session_id.reset(token2)

    def test_server_hook_auto_confirms_without_context(self):
        """Test that server hook auto-confirms when no context is set."""
        from gptme.hooks.server_confirm import server_confirm_hook
        from gptme.tools.base import ToolUse

        tool_use = ToolUse(tool="shell", args=[], content="echo test")

        # Without context vars set, should auto-confirm
        result = server_confirm_hook(tool_use)
        assert result.action == ConfirmAction.CONFIRM
