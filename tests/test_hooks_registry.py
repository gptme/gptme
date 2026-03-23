"""Unit tests for the hook registry and execution infrastructure.

Tests registration, unregistration, triggering, priority ordering,
enable/disable, async hooks, StopPropagation, error handling,
context-local isolation, and module-level API functions in
gptme/hooks/registry.py.
"""

import logging
import threading
import unittest.mock
from typing import Any

import pytest

from gptme.hooks.registry import (
    HookRegistry,
    clear_hooks,
    disable_hook,
    enable_hook,
    get_hooks,
    get_registry,
    register_hook,
    set_registry,
    trigger_hook,
    unregister_hook,
)
from gptme.hooks.types import Hook, HookType, StopPropagation
from gptme.message import Message

# ── Helpers ──────────────────────────────────────────────────────────


def _make_hook_func(
    messages: list[Message] | None = None,
) -> Any:
    """Create a simple hook function that yields given messages."""

    def hook_func(*args: Any, **kwargs: Any) -> Any:
        if messages:
            yield from messages

    return hook_func


def _make_noop_hook() -> Any:
    """Hook that returns None (no messages)."""

    def hook_func(*args: Any, **kwargs: Any) -> None:
        return None

    return hook_func


def _make_stop_hook(msg_before: Message | None = None) -> Any:
    """Hook that yields StopPropagation, optionally preceded by a message."""

    def hook_func(*args: Any, **kwargs: Any) -> Any:
        if msg_before:
            yield msg_before
        yield StopPropagation()

    return hook_func


def _make_error_hook(error: Exception) -> Any:
    """Hook that raises an exception."""

    def hook_func(*args: Any, **kwargs: Any) -> Any:
        raise error

    return hook_func


def _make_tracking_hook(tracker: list, label: str) -> Any:
    """Hook that appends to a tracker list when called, for ordering verification."""

    def hook_func(*args: Any, **kwargs: Any) -> None:
        tracker.append(label)

    return hook_func


def _make_message_returning_hook(msg: Message) -> Any:
    """Hook that returns a single Message (not a generator)."""

    def hook_func(*args: Any, **kwargs: Any) -> Message:
        return msg

    return hook_func


# ── HookRegistry: Registration ──────────────────────────────────────


class TestRegistration:
    """Tests for hook registration."""

    def test_register_single_hook(self):
        registry = HookRegistry()
        func = _make_noop_hook()
        registry.register("test-hook", HookType.SESSION_START, func)

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].name == "test-hook"
        assert hooks[0].hook_type == HookType.SESSION_START
        assert hooks[0].func is func
        assert hooks[0].priority == 0
        assert hooks[0].enabled is True
        assert hooks[0].async_mode is False

    def test_register_with_priority(self):
        registry = HookRegistry()
        registry.register("low", HookType.SESSION_START, _make_noop_hook(), priority=1)
        registry.register(
            "high", HookType.SESSION_START, _make_noop_hook(), priority=10
        )

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 2
        # Higher priority should come first (sorted descending)
        assert hooks[0].name == "high"
        assert hooks[1].name == "low"

    def test_register_disabled_hook(self):
        registry = HookRegistry()
        registry.register(
            "disabled-hook", HookType.SESSION_START, _make_noop_hook(), enabled=False
        )

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].enabled is False

    def test_register_async_hook(self):
        registry = HookRegistry()
        registry.register(
            "async-hook",
            HookType.SESSION_START,
            _make_noop_hook(),
            async_mode=True,
        )

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].async_mode is True

    def test_register_replaces_existing_same_name(self):
        registry = HookRegistry()
        func1 = _make_noop_hook()
        func2 = _make_noop_hook()

        registry.register("my-hook", HookType.SESSION_START, func1)
        registry.register("my-hook", HookType.SESSION_START, func2)

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].func is func2  # replaced with the new one

    def test_register_multiple_types(self):
        registry = HookRegistry()
        registry.register("hook-a", HookType.SESSION_START, _make_noop_hook())
        registry.register("hook-b", HookType.SESSION_END, _make_noop_hook())

        assert len(registry.get_hooks(HookType.SESSION_START)) == 1
        assert len(registry.get_hooks(HookType.SESSION_END)) == 1

    def test_register_multiple_same_type(self):
        registry = HookRegistry()
        registry.register("hook-a", HookType.SESSION_START, _make_noop_hook())
        registry.register("hook-b", HookType.SESSION_START, _make_noop_hook())
        registry.register("hook-c", HookType.SESSION_START, _make_noop_hook())

        assert len(registry.get_hooks(HookType.SESSION_START)) == 3


# ── HookRegistry: Unregistration ────────────────────────────────────


class TestUnregistration:
    """Tests for hook unregistration."""

    def test_unregister_by_name_and_type(self):
        registry = HookRegistry()
        registry.register("hook-a", HookType.SESSION_START, _make_noop_hook())
        registry.register("hook-b", HookType.SESSION_START, _make_noop_hook())

        registry.unregister("hook-a", HookType.SESSION_START)
        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].name == "hook-b"

    def test_unregister_by_name_all_types(self):
        registry = HookRegistry()
        registry.register("shared-name", HookType.SESSION_START, _make_noop_hook())
        registry.register("shared-name", HookType.SESSION_END, _make_noop_hook())

        registry.unregister("shared-name")  # no type filter
        assert len(registry.get_hooks(HookType.SESSION_START)) == 0
        assert len(registry.get_hooks(HookType.SESSION_END)) == 0

    def test_unregister_nonexistent_name(self):
        registry = HookRegistry()
        registry.register("exists", HookType.SESSION_START, _make_noop_hook())
        # Should not raise
        registry.unregister("does-not-exist", HookType.SESSION_START)
        assert len(registry.get_hooks(HookType.SESSION_START)) == 1

    def test_unregister_wrong_type(self):
        registry = HookRegistry()
        registry.register("my-hook", HookType.SESSION_START, _make_noop_hook())

        registry.unregister("my-hook", HookType.SESSION_END)
        # Hook should still exist in SESSION_START
        assert len(registry.get_hooks(HookType.SESSION_START)) == 1


# ── HookRegistry: get_hooks ─────────────────────────────────────────


class TestGetHooks:
    """Tests for getting hooks from the registry."""

    def test_get_hooks_empty_registry(self):
        registry = HookRegistry()
        assert registry.get_hooks() == []
        assert registry.get_hooks(HookType.SESSION_START) == []

    def test_get_hooks_filtered_by_type(self):
        registry = HookRegistry()
        registry.register("a", HookType.SESSION_START, _make_noop_hook())
        registry.register("b", HookType.SESSION_END, _make_noop_hook())

        start_hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(start_hooks) == 1
        assert start_hooks[0].name == "a"

    def test_get_hooks_all(self):
        registry = HookRegistry()
        registry.register("a", HookType.SESSION_START, _make_noop_hook())
        registry.register("b", HookType.SESSION_END, _make_noop_hook())
        registry.register("c", HookType.STEP_PRE, _make_noop_hook())

        all_hooks = registry.get_hooks()
        assert len(all_hooks) == 3


# ── HookRegistry: Enable/Disable ────────────────────────────────────


class TestEnableDisable:
    """Tests for enabling and disabling hooks."""

    def test_disable_hook(self):
        registry = HookRegistry()
        registry.register("my-hook", HookType.SESSION_START, _make_noop_hook())

        registry.disable_hook("my-hook")
        hooks = registry.get_hooks(HookType.SESSION_START)
        assert hooks[0].enabled is False

    def test_enable_hook(self):
        registry = HookRegistry()
        registry.register(
            "my-hook", HookType.SESSION_START, _make_noop_hook(), enabled=False
        )

        registry.enable_hook("my-hook")
        hooks = registry.get_hooks(HookType.SESSION_START)
        assert hooks[0].enabled is True

    def test_disable_with_type_filter(self):
        registry = HookRegistry()
        registry.register("hook", HookType.SESSION_START, _make_noop_hook())
        registry.register("hook", HookType.SESSION_END, _make_noop_hook())

        # Disable only for SESSION_START
        registry.disable_hook("hook", HookType.SESSION_START)

        start_hooks = registry.get_hooks(HookType.SESSION_START)
        end_hooks = registry.get_hooks(HookType.SESSION_END)
        assert start_hooks[0].enabled is False
        assert end_hooks[0].enabled is True

    def test_disable_nonexistent_hook(self):
        registry = HookRegistry()
        # Should not raise
        registry.disable_hook("nonexistent")

    def test_enable_nonexistent_hook(self):
        registry = HookRegistry()
        # Should not raise
        registry.enable_hook("nonexistent")


# ── HookRegistry: Trigger (sync) ────────────────────────────────────


class TestTriggerSync:
    """Tests for triggering sync hooks."""

    def test_trigger_no_hooks(self):
        registry = HookRegistry()
        messages = list(registry.trigger(HookType.SESSION_START))
        assert messages == []

    def test_trigger_yields_messages(self):
        registry = HookRegistry()
        msg = Message("system", "hello from hook")
        registry.register("msg-hook", HookType.STEP_PRE, _make_hook_func([msg]))

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 1
        assert messages[0].content == "hello from hook"

    def test_trigger_multiple_messages(self):
        registry = HookRegistry()
        msg1 = Message("system", "first")
        msg2 = Message("system", "second")
        registry.register("multi-msg", HookType.STEP_PRE, _make_hook_func([msg1, msg2]))

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 2
        assert messages[0].content == "first"
        assert messages[1].content == "second"

    def test_trigger_noop_hook(self):
        registry = HookRegistry()
        registry.register("noop", HookType.STEP_PRE, _make_noop_hook())

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_trigger_skips_disabled_hooks(self):
        registry = HookRegistry()
        msg = Message("system", "should not appear")
        registry.register(
            "disabled", HookType.STEP_PRE, _make_hook_func([msg]), enabled=False
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_trigger_respects_priority_order(self):
        registry = HookRegistry()
        tracker: list[str] = []

        registry.register(
            "low", HookType.STEP_PRE, _make_tracking_hook(tracker, "low"), priority=1
        )
        registry.register(
            "high", HookType.STEP_PRE, _make_tracking_hook(tracker, "high"), priority=10
        )
        registry.register(
            "med", HookType.STEP_PRE, _make_tracking_hook(tracker, "med"), priority=5
        )

        list(registry.trigger(HookType.STEP_PRE))
        assert tracker == ["high", "med", "low"]

    def test_trigger_message_return_not_generator(self):
        """Hook that returns a Message directly (not via yield)."""
        registry = HookRegistry()
        msg = Message("system", "returned directly")
        registry.register(
            "direct-return",
            HookType.STEP_PRE,
            _make_message_returning_hook(msg),
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 1
        assert messages[0].content == "returned directly"

    def test_trigger_passes_args_to_hooks(self):
        """Verify that trigger passes positional and keyword arguments."""
        registry = HookRegistry()
        received_args: dict[str, Any] = {}

        def capturing_hook(*args: Any, **kwargs: Any) -> None:
            received_args["args"] = args
            received_args["kwargs"] = kwargs

        registry.register("capture", HookType.STEP_PRE, capturing_hook)
        list(registry.trigger(HookType.STEP_PRE, "arg1", "arg2", key="val"))

        assert received_args["args"] == ("arg1", "arg2")
        assert received_args["kwargs"] == {"key": "val"}

    def test_trigger_wrong_type_returns_nothing(self):
        registry = HookRegistry()
        registry.register("hook", HookType.SESSION_START, _make_noop_hook())

        # Trigger a different type
        messages = list(registry.trigger(HookType.SESSION_END))
        assert messages == []

    def test_trigger_multiple_hooks_aggregate_messages(self):
        registry = HookRegistry()
        msg1 = Message("system", "from hook 1")
        msg2 = Message("system", "from hook 2")

        registry.register("hook-1", HookType.STEP_PRE, _make_hook_func([msg1]))
        registry.register("hook-2", HookType.STEP_PRE, _make_hook_func([msg2]))

        messages = list(registry.trigger(HookType.STEP_PRE))
        contents = [m.content for m in messages]
        assert "from hook 1" in contents
        assert "from hook 2" in contents


# ── HookRegistry: StopPropagation ────────────────────────────────────


class TestStopPropagation:
    """Tests for StopPropagation handling."""

    def test_stop_propagation_from_generator(self):
        """StopPropagation yielded from a generator stops remaining hooks."""
        registry = HookRegistry()
        tracker: list[str] = []

        registry.register(
            "first",
            HookType.STEP_PRE,
            _make_stop_hook(),
            priority=10,
        )
        registry.register(
            "second",
            HookType.STEP_PRE,
            _make_tracking_hook(tracker, "second"),
            priority=1,
        )

        list(registry.trigger(HookType.STEP_PRE))
        assert "second" not in tracker  # second hook should NOT have run

    def test_stop_propagation_with_message_before(self):
        """Messages yielded before StopPropagation are still collected."""
        registry = HookRegistry()
        msg = Message("system", "before stop")

        registry.register(
            "stop-with-msg",
            HookType.STEP_PRE,
            _make_stop_hook(msg_before=msg),
            priority=10,
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 1
        assert messages[0].content == "before stop"

    def test_stop_propagation_returned_directly(self):
        """Hook that returns StopPropagation directly (not via yield)."""
        registry = HookRegistry()
        tracker: list[str] = []

        def stop_hook(*args, **kwargs):
            return StopPropagation()

        registry.register("stopper", HookType.STEP_PRE, stop_hook, priority=10)
        registry.register(
            "after",
            HookType.STEP_PRE,
            _make_tracking_hook(tracker, "after"),
            priority=1,
        )

        list(registry.trigger(HookType.STEP_PRE))
        assert "after" not in tracker


# ── HookRegistry: Error Handling ─────────────────────────────────────


class TestErrorHandling:
    """Tests for error handling in hook execution."""

    def test_exception_in_hook_continues_others(self):
        """An exception in one hook should not prevent others from running."""
        registry = HookRegistry()
        tracker: list[str] = []

        registry.register(
            "error-hook",
            HookType.STEP_PRE,
            _make_error_hook(ValueError("test error")),
            priority=10,
        )
        registry.register(
            "good-hook",
            HookType.STEP_PRE,
            _make_tracking_hook(tracker, "good"),
            priority=1,
        )

        # Should not raise
        list(registry.trigger(HookType.STEP_PRE))
        assert "good" in tracker

    def test_session_complete_exception_propagates(self):
        """SessionCompleteException should propagate (not be swallowed)."""
        registry = HookRegistry()

        # Create a fake SessionCompleteException
        class SessionCompleteException(Exception):
            pass

        registry.register(
            "session-end",
            HookType.STEP_PRE,
            _make_error_hook(SessionCompleteException("done")),
        )

        with pytest.raises(SessionCompleteException):
            list(registry.trigger(HookType.STEP_PRE))


# ── HookRegistry: Async Hooks ───────────────────────────────────────


class TestAsyncHooks:
    """Tests for async (background thread) hook execution."""

    def test_async_hook_runs_in_background(self):
        """Async hooks should run without blocking the main thread."""
        registry = HookRegistry()
        event = threading.Event()

        def async_func(*args, **kwargs):
            event.set()
            return

        registry.register(
            "async-hook",
            HookType.STEP_PRE,
            async_func,
            async_mode=True,
        )

        # Trigger should return immediately (no messages from async hooks)
        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

        # Wait for async hook to complete
        assert event.wait(timeout=2.0), "Async hook did not execute"

    def test_async_hook_does_not_yield_messages(self):
        """Messages from async hooks should be logged, not yielded."""
        registry = HookRegistry()
        event = threading.Event()

        def async_msg_hook(*args, **kwargs):
            event.set()
            yield Message("system", "async message")

        registry.register(
            "async-msg",
            HookType.STEP_PRE,
            async_msg_hook,
            async_mode=True,
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []  # async messages are logged, not yielded
        assert event.wait(timeout=2.0), "Async hook did not execute"

    def test_async_and_sync_hooks_together(self):
        """Sync hooks yield messages; async hooks run in background."""
        registry = HookRegistry()
        event = threading.Event()
        sync_msg = Message("system", "sync message")

        def async_func(*args, **kwargs):
            event.set()
            return

        registry.register(
            "sync-hook",
            HookType.STEP_PRE,
            _make_hook_func([sync_msg]),
        )
        registry.register(
            "async-hook",
            HookType.STEP_PRE,
            async_func,
            async_mode=True,
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 1
        assert messages[0].content == "sync message"
        assert event.wait(timeout=2.0)

    def test_async_hook_error_does_not_crash(self):
        """Errors in async hooks should be logged, not propagated."""
        registry = HookRegistry()
        error_event = threading.Event()

        def failing_async(*args, **kwargs):
            error_event.set()
            raise ValueError("async failure")

        registry.register(
            "failing-async",
            HookType.STEP_PRE,
            failing_async,
            async_mode=True,
        )

        # Should not raise
        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []
        # Give thread time to complete
        assert error_event.wait(timeout=2.0), "Async error hook did not execute"


# ── HookRegistry: Priority Sorting (Hook.__lt__) ────────────────────


class TestHookSorting:
    """Tests for Hook dataclass sorting behavior."""

    def test_higher_priority_first(self):
        h1 = Hook(
            name="a", hook_type=HookType.STEP_PRE, func=_make_noop_hook(), priority=1
        )
        h2 = Hook(
            name="b", hook_type=HookType.STEP_PRE, func=_make_noop_hook(), priority=10
        )

        sorted_hooks = sorted([h1, h2])
        assert sorted_hooks[0].name == "b"  # priority 10 first
        assert sorted_hooks[1].name == "a"  # priority 1 second

    def test_same_priority_sorted_by_name(self):
        """Same priority hooks should be sorted by name (alphabetical, reversed for __lt__)."""
        h1 = Hook(
            name="alpha",
            hook_type=HookType.STEP_PRE,
            func=_make_noop_hook(),
            priority=5,
        )
        h2 = Hook(
            name="beta", hook_type=HookType.STEP_PRE, func=_make_noop_hook(), priority=5
        )

        sorted_hooks = sorted([h1, h2])
        # __lt__ uses > comparison on (priority, name) tuple
        # So higher name string sorts first at same priority
        assert sorted_hooks[0].name == "beta"
        assert sorted_hooks[1].name == "alpha"

    def test_sorting_with_three_priorities(self):
        hooks = [
            Hook(
                name="low",
                hook_type=HookType.STEP_PRE,
                func=_make_noop_hook(),
                priority=1,
            ),
            Hook(
                name="high",
                hook_type=HookType.STEP_PRE,
                func=_make_noop_hook(),
                priority=100,
            ),
            Hook(
                name="med",
                hook_type=HookType.STEP_PRE,
                func=_make_noop_hook(),
                priority=50,
            ),
        ]
        sorted_hooks = sorted(hooks)
        assert [h.name for h in sorted_hooks] == ["high", "med", "low"]


# ── HookRegistry: clear_hooks (instance method via module) ───────────


class TestClearHooks:
    """Tests for clearing hooks."""

    def test_clear_all_hooks(self):
        registry = HookRegistry()
        registry.register("a", HookType.SESSION_START, _make_noop_hook())
        registry.register("b", HookType.SESSION_END, _make_noop_hook())

        registry.unregister("a")
        registry.unregister("b")
        assert registry.get_hooks() == []

    def test_clear_by_type(self):
        registry = HookRegistry()
        registry.register("a", HookType.SESSION_START, _make_noop_hook())
        registry.register("b", HookType.SESSION_END, _make_noop_hook())

        registry.unregister("a", HookType.SESSION_START)
        assert len(registry.get_hooks(HookType.SESSION_START)) == 0
        assert len(registry.get_hooks(HookType.SESSION_END)) == 1


# ── Module-level API ─────────────────────────────────────────────────


class TestModuleAPI:
    """Tests for module-level convenience functions."""

    def setup_method(self):
        """Reset registry before each test."""
        set_registry(HookRegistry())

    def test_get_registry_returns_existing(self):
        """get_registry should return the already-set registry."""
        registry = get_registry()
        assert isinstance(registry, HookRegistry)

    def test_set_and_get_registry(self):
        new_registry = HookRegistry()
        set_registry(new_registry)
        assert get_registry() is new_registry

    def test_register_hook_module_level(self):
        register_hook("test", HookType.SESSION_START, _make_noop_hook())
        hooks = get_hooks(HookType.SESSION_START)
        assert len(hooks) == 1
        assert hooks[0].name == "test"

    def test_unregister_hook_module_level(self):
        register_hook("test", HookType.SESSION_START, _make_noop_hook())
        unregister_hook("test", HookType.SESSION_START)
        assert len(get_hooks(HookType.SESSION_START)) == 0

    def test_trigger_hook_module_level(self):
        msg = Message("system", "module-level trigger")
        register_hook("test", HookType.STEP_PRE, _make_hook_func([msg]))
        messages = list(trigger_hook(HookType.STEP_PRE))
        assert len(messages) == 1
        assert messages[0].content == "module-level trigger"

    def test_enable_disable_hook_module_level(self):
        register_hook("test", HookType.SESSION_START, _make_noop_hook())

        disable_hook("test")
        hooks = get_hooks(HookType.SESSION_START)
        assert hooks[0].enabled is False

        enable_hook("test")
        hooks = get_hooks(HookType.SESSION_START)
        assert hooks[0].enabled is True

    def test_clear_hooks_module_level(self):
        register_hook("a", HookType.SESSION_START, _make_noop_hook())
        register_hook("b", HookType.SESSION_END, _make_noop_hook())

        clear_hooks()
        assert get_hooks() == []

    def test_clear_hooks_by_type_module_level(self):
        register_hook("a", HookType.SESSION_START, _make_noop_hook())
        register_hook("b", HookType.SESSION_END, _make_noop_hook())

        clear_hooks(HookType.SESSION_START)
        assert len(get_hooks(HookType.SESSION_START)) == 0
        assert len(get_hooks(HookType.SESSION_END)) == 1


# ── Context Isolation ────────────────────────────────────────────────


class TestContextIsolation:
    """Tests for ContextVar-based registry isolation."""

    def test_different_threads_get_own_registry(self):
        """Each thread should get its own registry via ContextVar."""
        results = {}

        def thread_func(thread_id):
            # Set a fresh registry in this thread's context to test ContextVar isolation:
            # ContextVar.set() only updates the calling context, so other threads'
            # registries are unaffected.
            set_registry(HookRegistry())
            registry = get_registry()
            registry.register(
                f"hook-{thread_id}",
                HookType.SESSION_START,
                _make_noop_hook(),
            )
            results[thread_id] = len(registry.get_hooks())

        # Reset main thread registry
        set_registry(HookRegistry())

        t1 = threading.Thread(target=thread_func, args=(1,))
        t2 = threading.Thread(target=thread_func, args=(2,))

        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Each thread should have exactly 1 hook (their own)
        assert results[1] == 1
        assert results[2] == 1

        # Main thread's registry should be separate
        main_hooks = get_registry().get_hooks()
        assert len(main_hooks) == 0


# ── Thread Safety ────────────────────────────────────────────────────


class TestThreadSafety:
    """Tests for thread-safe registration."""

    def test_concurrent_registration(self):
        """Multiple threads registering hooks concurrently should not lose hooks."""
        registry = HookRegistry()
        n_threads = 10

        def register_in_thread(i):
            registry.register(f"hook-{i}", HookType.SESSION_START, _make_noop_hook())

        threads = [
            threading.Thread(target=register_in_thread, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        hooks = registry.get_hooks(HookType.SESSION_START)
        assert len(hooks) == n_threads

    def test_concurrent_register_unregister(self):
        """Concurrent registration and unregistration should not crash."""
        registry = HookRegistry()
        errors = []

        def registerer():
            try:
                for i in range(20):
                    registry.register(
                        f"hook-{i}", HookType.SESSION_START, _make_noop_hook()
                    )
            except Exception as e:
                errors.append(e)

        def unregisterer():
            try:
                for i in range(20):
                    registry.unregister(f"hook-{i}", HookType.SESSION_START)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=registerer)
        t2 = threading.Thread(target=unregisterer)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert errors == [], f"Thread safety errors: {errors}"


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases and unusual scenarios."""

    def test_hook_returning_string_not_iterated(self):
        """A hook that returns a string should not have its chars yielded."""
        registry = HookRegistry()

        def string_hook(*args, **kwargs):
            return "not a message"

        registry.register("str-hook", HookType.STEP_PRE, string_hook)
        messages = list(registry.trigger(HookType.STEP_PRE))
        # String should not be treated as iterable messages
        assert messages == []

    def test_hook_returning_bytes_not_iterated(self):
        """A hook that returns bytes should not have its bytes yielded."""
        registry = HookRegistry()

        def bytes_hook(*args, **kwargs):
            return b"not a message"

        registry.register("bytes-hook", HookType.STEP_PRE, bytes_hook)
        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_hook_returning_non_iterable_object(self):
        """A hook that returns a non-iterable, non-Message should be ignored."""
        registry = HookRegistry()

        def int_hook(*args, **kwargs):
            return 42

        registry.register("int-hook", HookType.STEP_PRE, int_hook)
        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_trigger_on_empty_hook_list(self):
        """Triggering a type with no hooks registered should return empty."""
        registry = HookRegistry()
        # Register for one type but trigger another (STEP_PRE has no hooks)
        registry.register("a", HookType.SESSION_START, _make_noop_hook())

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_hook_with_all_disabled(self):
        """All hooks disabled for a type should yield no messages."""
        registry = HookRegistry()
        msg = Message("system", "should not appear")

        registry.register(
            "d1", HookType.STEP_PRE, _make_hook_func([msg]), enabled=False
        )
        registry.register(
            "d2", HookType.STEP_PRE, _make_hook_func([msg]), enabled=False
        )

        messages = list(registry.trigger(HookType.STEP_PRE))
        assert messages == []

    def test_slow_hook_logged(self, caplog):
        """Hooks taking > 5s should log a warning."""
        registry = HookRegistry()

        def noop_hook(*args, **kwargs):
            return None

        registry.register("slow", HookType.STEP_PRE, noop_hook)

        # Mock time() to simulate a hook that takes 6 seconds
        with (
            unittest.mock.patch("gptme.hooks.registry.time", side_effect=[0.0, 6.0]),
            caplog.at_level(logging.WARNING, logger="gptme.hooks.registry"),
        ):
            list(registry.trigger(HookType.STEP_PRE))

        assert any("long time" in msg for msg in caplog.messages)

    def test_hook_generator_with_mixed_types(self):
        """Generator yielding both Messages and non-Messages."""
        registry = HookRegistry()

        def mixed_hook(*args, **kwargs):
            yield Message("system", "valid message")
            yield 42  # not a Message, should be skipped
            yield Message("system", "another valid")

        registry.register("mixed", HookType.STEP_PRE, mixed_hook)
        messages = list(registry.trigger(HookType.STEP_PRE))
        assert len(messages) == 2
        assert messages[0].content == "valid message"
        assert messages[1].content == "another valid"
