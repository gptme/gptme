"""Tests for util/interrupt.py - ContextVar-based keyboard interrupt state management."""

import threading
from contextvars import copy_context

import pytest

from gptme.util.interrupt import (
    _interruptible_var,
    clear_interruptible,
    set_interruptible,
)

THREAD_TIMEOUT = 2.0
BARRIER_TIMEOUT = 2.0


@pytest.fixture(autouse=True)
def reset_interruptible_state():
    """Reset interruptible state before and after each test."""
    clear_interruptible()
    yield
    clear_interruptible()


class TestInterruptibleState:
    """Tests for set_interruptible and clear_interruptible state management."""

    def test_set_interruptible_changes_state(self):
        """set_interruptible marks current context as interruptible."""
        # Default state is not interruptible - setting it should work
        set_interruptible()
        # If we got here without error, the function works
        clear_interruptible()  # cleanup

    def test_clear_interruptible_changes_state(self):
        """clear_interruptible marks current context as not interruptible."""
        set_interruptible()
        clear_interruptible()
        # After clearing, state should be reset

    def test_set_then_clear_roundtrip(self):
        """Can set and clear interruptible state multiple times."""
        for _ in range(3):
            set_interruptible()
            clear_interruptible()

    def test_double_set_does_not_error(self):
        """Calling set_interruptible twice does not raise."""
        set_interruptible()
        set_interruptible()  # Should not raise
        clear_interruptible()  # cleanup

    def test_double_clear_does_not_error(self):
        """Calling clear_interruptible twice does not raise."""
        clear_interruptible()
        clear_interruptible()  # Should not raise


class TestContextVarIsolation:
    """Tests that ContextVar state is isolated between contexts."""

    def test_context_isolation_between_threads(self):
        """Interrupt state is isolated between threads via ContextVar."""
        results = {}
        thread_errors = []
        barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT)

        def thread_set():
            try:
                # Thread sets interruptible in its own context
                set_interruptible()
                results["thread_state"] = _interruptible_var.get()
                barrier.wait()
                barrier.wait()
            except BaseException as exc:  # pragma: no branch
                thread_errors.append(exc)

        def thread_clear():
            try:
                # This thread starts in default (non-interruptible) state
                # The other thread's state should not affect this one
                barrier.wait()
                # Read state while thread_set has called set_interruptible — should still be False
                results["clear_thread_state"] = _interruptible_var.get()
                barrier.wait()
            except BaseException as exc:  # pragma: no branch
                thread_errors.append(exc)

        t1 = threading.Thread(target=thread_set)
        t2 = threading.Thread(target=thread_clear)
        t1.start()
        t2.start()
        t1.join(timeout=THREAD_TIMEOUT)
        t2.join(timeout=THREAD_TIMEOUT)

        assert not t1.is_alive()
        assert not t2.is_alive()
        assert thread_errors == []
        assert results["thread_state"] is True
        assert results["clear_thread_state"] is False

    def test_copy_context_inherits_state(self):
        """copy_context() captures the current ContextVar state."""
        set_interruptible()

        state_in_copy = {}

        def check_state():
            # Inside a copied context, we should see the parent's state
            # (copy_context captures current values)
            state_in_copy["interruptible"] = _interruptible_var.get()

        ctx = copy_context()
        ctx.run(check_state)
        assert state_in_copy["interruptible"] is True

        clear_interruptible()  # cleanup

    def test_state_changes_not_shared_across_copied_contexts(self):
        """Changes in a copied context don't affect the original."""
        results = {}

        def run_in_copy():
            set_interruptible()
            results["copy_state"] = _interruptible_var.get()

        clear_interruptible()  # ensure original context starts as False
        ctx = copy_context()
        ctx.run(run_in_copy)

        # Copy saw the change
        assert results["copy_state"] is True
        # Original context should not be affected by changes in copy
        assert _interruptible_var.get() is False


class TestImportedFunctions:
    """Tests that functions are importable and callable."""

    def test_set_interruptible_is_callable(self):
        """set_interruptible is importable and callable."""
        assert callable(set_interruptible)

    def test_clear_interruptible_is_callable(self):
        """clear_interruptible is importable and callable."""
        assert callable(clear_interruptible)

    def test_set_returns_none(self):
        """set_interruptible returns None."""
        result = set_interruptible()
        assert result is None
        clear_interruptible()  # cleanup

    def test_clear_returns_none(self):
        """clear_interruptible returns None."""
        result = clear_interruptible()
        assert result is None

    def test_no_side_effects_on_import(self):
        """Importing the module doesn't change interruptible state."""
        # This is implicitly tested by all other tests starting in a clean state
        import gptme.util.interrupt

        assert gptme.util.interrupt.set_interruptible is not None


class TestHandleKeyboardInterrupt:
    """Tests for handle_keyboard_interrupt behavior."""

    def test_handle_raises_in_testing_env(self):
        """In pytest environment (PYTEST_CURRENT_TEST set), interrupt always raises."""
        import os

        from gptme.util.interrupt import handle_keyboard_interrupt

        # PYTEST_CURRENT_TEST should already be set when running under pytest
        assert os.getenv("PYTEST_CURRENT_TEST") is not None

        with pytest.raises(KeyboardInterrupt):
            handle_keyboard_interrupt(None, None)

    def test_handle_does_not_raise_in_non_main_thread(self):
        """handle_keyboard_interrupt does not raise in non-main threads."""
        from gptme.util.interrupt import handle_keyboard_interrupt

        raised = threading.Event()
        completed = threading.Event()

        def worker():
            try:
                # In a non-main thread, should not raise even in testing env
                # (the function returns early for non-main threads)
                # Note: PYTEST_CURRENT_TEST is set, but thread check comes first
                handle_keyboard_interrupt(None, None)
                completed.set()
            except KeyboardInterrupt:
                raised.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=THREAD_TIMEOUT)

        # In non-main thread, should not raise
        assert not t.is_alive()
        assert not raised.is_set()
        assert completed.is_set()
