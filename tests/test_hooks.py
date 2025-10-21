"""Tests for the hook system."""

import pytest

from gptme.hooks import (
    HookType,
    clear_hooks,
    disable_hook,
    enable_hook,
    get_hooks,
    register_hook,
    trigger_hook,
    unregister_hook,
)
from gptme.message import Message


@pytest.fixture(autouse=True)
def clear_all_hooks():
    """Clear all hooks before and after each test."""
    clear_hooks()
    yield
    clear_hooks()


def test_register_hook():
    """Test hook registration."""

    def my_hook(log, workspace, manager=None):
        yield Message("system", "Hook called")

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)

    hooks = get_hooks(HookType.MESSAGE_PRE_PROCESS)
    assert len(hooks) == 1
    assert hooks[0].name == "test_hook"


def test_trigger_hook():
    """Test hook triggering."""
    messages = []

    def my_hook(log, workspace, manager=None):
        messages.append("called")
        yield Message("system", "Hook result")

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)

    results = list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert len(messages) == 1
    assert messages[0] == "called"
    assert len(results) == 1
    assert results[0].content == "Hook result"


def test_hook_priority():
    """Test that hooks run in priority order (higher priority first)."""
    call_order = []

    def hook_low(log, workspace, manager=None):
        call_order.append("low")
        if False:
            yield

    def hook_high(log, workspace, manager=None):
        call_order.append("high")
        if False:
            yield

    def hook_medium(log, workspace, manager=None):
        call_order.append("medium")
        if False:
            yield

    register_hook("low", HookType.MESSAGE_PRE_PROCESS, hook_low, priority=1)
    register_hook("high", HookType.MESSAGE_PRE_PROCESS, hook_high, priority=10)
    register_hook("medium", HookType.MESSAGE_PRE_PROCESS, hook_medium, priority=5)

    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert call_order == ["high", "medium", "low"]


def test_hook_enable_disable():
    """Test enabling and disabling hooks."""
    messages = []

    def my_hook(log, workspace, manager=None):
        messages.append("called")
        if False:
            yield

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)

    # Hook should be enabled by default
    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))
    assert len(messages) == 1

    # Disable hook
    disable_hook("test_hook")
    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))
    assert len(messages) == 1  # Still 1, hook didn't run

    # Re-enable hook
    enable_hook("test_hook")
    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))
    assert len(messages) == 2  # Hook ran again


def test_unregister_hook():
    """Test unregistering hooks."""

    def my_hook(log, workspace, manager=None):
        if False:
            yield

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)
    assert len(get_hooks(HookType.MESSAGE_PRE_PROCESS)) == 1

    unregister_hook("test_hook", HookType.MESSAGE_PRE_PROCESS)
    assert len(get_hooks(HookType.MESSAGE_PRE_PROCESS)) == 0


def test_unregister_from_all_types():
    """Test unregistering a hook from all types."""

    def my_hook(log, workspace, manager=None):
        if False:
            yield

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)
    register_hook("test_hook", HookType.MESSAGE_POST_PROCESS, my_hook)

    assert len(get_hooks(HookType.MESSAGE_PRE_PROCESS)) == 1
    assert len(get_hooks(HookType.MESSAGE_POST_PROCESS)) == 1

    unregister_hook("test_hook")  # Remove from all types

    assert len(get_hooks(HookType.MESSAGE_PRE_PROCESS)) == 0
    assert len(get_hooks(HookType.MESSAGE_POST_PROCESS)) == 0


def test_hook_with_arguments():
    """Test hooks receive arguments correctly."""
    received_args = {}

    def my_hook(log, workspace, tool_use):
        received_args.update({"log": log, "workspace": workspace, "tool_use": tool_use})
        if False:
            yield

    register_hook("test_hook", HookType.TOOL_PRE_EXECUTE, my_hook)

    # Create a mock ToolUse for testing
    from gptme.tools.base import ToolUse

    tool_use = ToolUse(tool="save", args=[], content=None)
    list(
        trigger_hook(
            HookType.TOOL_PRE_EXECUTE, log=None, workspace=None, tool_use=tool_use
        )
    )

    assert received_args["tool_use"].tool == "save"
    assert received_args["log"] is None
    assert received_args["workspace"] is None


def test_hook_generator():
    """Test hooks can yield multiple messages."""

    def my_hook(log, workspace, manager=None):
        yield Message("system", "First")
        yield Message("system", "Second")
        yield Message("system", "Third")

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)

    results = list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert len(results) == 3
    assert results[0].content == "First"
    assert results[1].content == "Second"
    assert results[2].content == "Third"


def test_hook_error_handling():
    """Test that hook errors are caught and don't break execution."""

    def failing_hook(log, workspace, manager=None):
        if False:
            yield
        raise ValueError("Hook error")

    def working_hook(log, workspace, manager=None):
        yield Message("system", "Success")

    register_hook("failing", HookType.MESSAGE_PRE_PROCESS, failing_hook, priority=10)
    register_hook("working", HookType.MESSAGE_PRE_PROCESS, working_hook, priority=5)

    results = list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    # Should have 1 message: the success message from working hook
    # Error messages are not yielded to prevent infinite loops
    assert len(results) == 1
    assert results[0].content == "Success"


def test_multiple_hooks_same_type():
    """Test multiple hooks of the same type."""
    messages = []

    def hook1(log, workspace, manager=None):
        messages.append("hook1")
        if False:
            yield

    def hook2(log, workspace, manager=None):
        messages.append("hook2")
        if False:
            yield

    register_hook("hook1", HookType.MESSAGE_PRE_PROCESS, hook1)
    register_hook("hook2", HookType.MESSAGE_PRE_PROCESS, hook2)

    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert "hook1" in messages
    assert "hook2" in messages
    assert len(messages) == 2


def test_replace_existing_hook():
    """Test that registering a hook with the same name replaces the old one."""
    messages = []

    def old_hook(log, workspace, manager=None):
        messages.append("old")
        if False:
            yield

    def new_hook(log, workspace, manager=None):
        messages.append("new")
        if False:
            yield

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, old_hook)
    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, new_hook)

    list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert messages == ["new"]  # Only new hook should run


def test_hook_no_return():
    """Test hooks that don't return anything."""
    called = []

    def my_hook(log, workspace, manager=None):
        called.append(True)
        # No return value
        if False:
            yield

    register_hook("test_hook", HookType.MESSAGE_PRE_PROCESS, my_hook)

    results = list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    assert len(called) == 1
    assert len(results) == 0  # No messages returned


def test_hook_stop_propagation():
    """Test that hooks can stop propagation of lower-priority hooks."""
    from gptme.hooks import StopPropagation

    execution_order = []

    def high_priority_hook(log, workspace, manager=None):
        execution_order.append("high")
        yield Message("system", "High priority")
        yield StopPropagation()  # Stop further hooks

    def low_priority_hook(log, workspace, manager=None):
        execution_order.append("low")
        yield Message("system", "Low priority")

    register_hook("high", HookType.MESSAGE_PRE_PROCESS, high_priority_hook, priority=10)
    register_hook("low", HookType.MESSAGE_PRE_PROCESS, low_priority_hook, priority=1)

    results = list(trigger_hook(HookType.MESSAGE_PRE_PROCESS))

    # Only high priority hook should have run
    assert execution_order == ["high"]
    # Only one message (from high priority hook)
    assert len(results) == 1
    assert results[0].content == "High priority"
