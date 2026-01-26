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

    def my_hook(manager):
        yield Message("system", "Hook called")

    register_hook("test_hook", HookType.STEP_PRE, my_hook)

    hooks = get_hooks(HookType.STEP_PRE)
    assert len(hooks) == 1
    assert hooks[0].name == "test_hook"


def test_trigger_hook():
    """Test hook triggering."""
    messages = []

    def my_hook(manager):
        messages.append("called")
        yield Message("system", "Hook result")

    register_hook("test_hook", HookType.STEP_PRE, my_hook)

    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert len(messages) == 1
    assert messages[0] == "called"
    assert len(results) == 1
    assert results[0].content == "Hook result"


def test_hook_priority():
    """Test that hooks run in priority order (higher priority first)."""
    call_order = []

    def hook_low(manager):
        call_order.append("low")
        return
        yield  # Make it a generator

    def hook_high(manager):
        call_order.append("high")
        return
        yield  # Make it a generator

    def hook_medium(manager):
        call_order.append("medium")
        return
        yield  # Make it a generator

    register_hook("low", HookType.STEP_PRE, hook_low, priority=1)
    register_hook("high", HookType.STEP_PRE, hook_high, priority=10)
    register_hook("medium", HookType.STEP_PRE, hook_medium, priority=5)

    list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert call_order == ["high", "medium", "low"]


def test_hook_enable_disable():
    """Test enabling and disabling hooks."""
    messages = []

    def my_hook(manager):
        messages.append("called")
        if False:
            yield

    register_hook("test_hook", HookType.STEP_PRE, my_hook)

    # Hook should be enabled by default
    list(trigger_hook(HookType.STEP_PRE, manager=None))
    assert len(messages) == 1

    # Disable hook
    disable_hook("test_hook")
    list(trigger_hook(HookType.STEP_PRE, manager=None))
    assert len(messages) == 1  # Still 1, hook didn't run

    # Re-enable hook
    enable_hook("test_hook")
    list(trigger_hook(HookType.STEP_PRE, manager=None))
    assert len(messages) == 2  # Hook ran again


def test_unregister_hook():
    """Test unregistering hooks."""

    def my_hook(manager):
        if False:
            yield

    register_hook("test_hook", HookType.STEP_PRE, my_hook)
    assert len(get_hooks(HookType.STEP_PRE)) == 1

    unregister_hook("test_hook", HookType.STEP_PRE)
    assert len(get_hooks(HookType.STEP_PRE)) == 0


def test_unregister_from_all_types():
    """Test unregistering a hook from all types."""

    def my_hook(manager):
        if False:
            yield

    register_hook("test_hook", HookType.STEP_PRE, my_hook)
    register_hook("test_hook", HookType.TURN_POST, my_hook)

    assert len(get_hooks(HookType.STEP_PRE)) == 1
    assert len(get_hooks(HookType.TURN_POST)) == 1

    unregister_hook("test_hook")  # Remove from all types

    assert len(get_hooks(HookType.STEP_PRE)) == 0
    assert len(get_hooks(HookType.TURN_POST)) == 0


def test_hook_with_arguments():
    """Test hooks receive arguments correctly."""
    received_args = {}

    def my_hook(log, workspace, tool_use):
        received_args.update({"log": log, "workspace": workspace, "tool_use": tool_use})
        if False:
            yield

    register_hook("test_hook", HookType.TOOL_EXECUTE_PRE, my_hook)

    # Create a mock ToolUse for testing
    from gptme.tools.base import ToolUse

    tool_use = ToolUse(tool="save", args=[], content=None)
    list(
        trigger_hook(
            HookType.TOOL_EXECUTE_PRE, log=None, workspace=None, tool_use=tool_use
        )
    )

    assert received_args["tool_use"].tool == "save"
    assert received_args["log"] is None
    assert received_args["workspace"] is None


def test_hook_generator():
    """Test hooks can yield multiple messages."""

    def my_hook(manager):
        yield Message("system", "First")
        yield Message("system", "Second")
        yield Message("system", "Third")

    register_hook("test_hook", HookType.STEP_PRE, my_hook)

    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert len(results) == 3
    assert results[0].content == "First"
    assert results[1].content == "Second"
    assert results[2].content == "Third"


def test_hook_error_handling():
    """Test that hook errors are caught and don't break execution."""

    def failing_hook(manager):
        if False:
            yield
        raise ValueError("Hook error")

    def working_hook(manager):
        yield Message("system", "Success")

    register_hook("failing", HookType.STEP_PRE, failing_hook, priority=10)
    register_hook("working", HookType.STEP_PRE, working_hook, priority=5)

    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    # Should have 1 message: the success message from working hook
    # Error messages are not yielded to prevent infinite loops
    assert len(results) == 1
    assert results[0].content == "Success"


def test_multiple_hooks_same_type():
    """Test multiple hooks of the same type."""
    messages = []

    def hook1(manager):
        messages.append("hook1")
        if False:
            yield

    def hook2(manager):
        messages.append("hook2")
        if False:
            yield

    register_hook("hook1", HookType.STEP_PRE, hook1)
    register_hook("hook2", HookType.STEP_PRE, hook2)

    list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert "hook1" in messages
    assert "hook2" in messages
    assert len(messages) == 2


def test_replace_existing_hook():
    """Test that registering a hook with the same name replaces the old one."""
    messages = []

    def old_hook(manager):
        messages.append("old")
        if False:
            yield

    def new_hook(manager):
        messages.append("new")
        if False:
            yield

    register_hook("test_hook", HookType.STEP_PRE, old_hook)
    register_hook("test_hook", HookType.STEP_PRE, new_hook)

    list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert messages == ["new"]  # Only new hook should run


def test_hook_no_return():
    """Test hooks that don't return anything."""
    called = []

    def my_hook(manager):
        called.append(True)
        # No return value
        if False:
            yield

    register_hook("test_hook", HookType.STEP_PRE, my_hook)

    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    assert len(called) == 1
    assert len(results) == 0  # No messages returned


def test_hook_stop_propagation():
    """Test that hooks can stop propagation of lower-priority hooks."""
    from gptme.hooks import StopPropagation

    execution_order = []

    def high_priority_hook(manager):
        execution_order.append("high")
        yield Message("system", "High priority")
        yield StopPropagation()  # Stop further hooks

    def low_priority_hook(manager):
        execution_order.append("low")
        yield Message("system", "Low priority")

    register_hook("high", HookType.STEP_PRE, high_priority_hook, priority=10)
    register_hook("low", HookType.STEP_PRE, low_priority_hook, priority=1)

    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    # Only high priority hook should have run
    assert execution_order == ["high"]
    # Only one message (from high priority hook)
    assert len(results) == 1
    assert results[0].content == "High priority"


def test_async_hook():
    """Test that async hooks run in background without blocking."""
    import time

    execution_log = []
    sync_completed = []

    def slow_async_hook(manager):
        """Async hook that takes some time."""
        time.sleep(0.1)  # Simulate slow operation
        execution_log.append("async_completed")
        # Messages from async hooks are logged, not yielded
        yield Message("system", "Async hook done")

    def fast_sync_hook(manager):
        """Sync hook that completes quickly."""
        sync_completed.append(True)
        execution_log.append("sync_completed")
        yield Message("system", "Sync hook done")

    # Register async hook (runs in background)
    register_hook("slow_async", HookType.STEP_PRE, slow_async_hook, async_mode=True)
    # Register sync hook (runs normally)
    register_hook("fast_sync", HookType.STEP_PRE, fast_sync_hook)

    # Trigger hooks - sync should complete immediately
    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    # Sync hook should have completed
    assert len(sync_completed) == 1
    # Only sync hook's message should be yielded
    assert len(results) == 1
    assert results[0].content == "Sync hook done"
    # At this point, sync completed but async might still be running
    assert "sync_completed" in execution_log

    # Wait a bit for async hook to complete
    time.sleep(0.2)

    # Now async should have completed too
    assert "async_completed" in execution_log


def test_async_hook_error_handling():
    """Test that errors in async hooks don't crash the system."""
    sync_completed = []

    def failing_async_hook(manager):
        """Async hook that raises an exception."""
        raise ValueError("Async hook failed!")

    def normal_sync_hook(manager):
        """Normal sync hook."""
        sync_completed.append(True)
        yield Message("system", "Sync hook done")

    # Register failing async hook
    register_hook(
        "failing_async", HookType.STEP_PRE, failing_async_hook, async_mode=True
    )
    # Register normal sync hook
    register_hook("normal_sync", HookType.STEP_PRE, normal_sync_hook)

    # Should not raise, async errors are caught and logged
    results = list(trigger_hook(HookType.STEP_PRE, manager=None))

    # Sync hook should still work
    assert len(sync_completed) == 1
    assert len(results) == 1


def test_async_hook_registration():
    """Test that async_mode is properly stored in hook registration."""

    def my_hook(manager):
        yield Message("system", "Test")

    # Register with async_mode=True
    register_hook("async_hook", HookType.STEP_PRE, my_hook, async_mode=True)

    hooks = get_hooks(HookType.STEP_PRE)
    assert len(hooks) == 1
    assert hooks[0].name == "async_hook"
    assert hooks[0].async_mode is True

    # Register without async_mode (default False)
    register_hook("sync_hook", HookType.STEP_POST, my_hook)

    hooks = get_hooks(HookType.STEP_POST)
    assert len(hooks) == 1
    assert hooks[0].name == "sync_hook"
    assert hooks[0].async_mode is False
