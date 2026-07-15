"""Tests for the retry-backoff interrupt signal (gptme/llm/retry_abort.py).

See retry_abort.py for the rationale: leaked subagent threads sleep through
LLM retry backoff (up to ~15s) far past the 2s conftest join timeout. The
generation counter (process-wide) and per-thread Events (scoped) let test
teardown make every backoff wait belonging to a leaked thread return
immediately — permanently, with no clear/reset window to slip through.
"""

import threading
import time
from unittest.mock import MagicMock

import pytest

from gptme.llm import retry_abort
from gptme.llm.retry_abort import (
    backoff_wait,
    bind_thread_generation,
    current_generation,
    interrupt_pending_retries,
    interrupt_thread,
    release_thread,
)


@pytest.fixture(autouse=True)
def _reset_retry_abort_state():
    """Reset global state before and after each test to isolate them."""
    # Reset before test
    retry_abort._generation = 0
    retry_abort._thread_events.clear()
    yield
    # Reset after test
    retry_abort._generation = 0
    retry_abort._thread_events.clear()


def test_backoff_wait_not_interrupted_waits_full_delay():
    start = time.monotonic()
    aborted = backoff_wait(0.05)
    elapsed = time.monotonic() - start

    assert aborted is False
    assert elapsed >= 0.04


def test_backoff_wait_interrupted_mid_wait_returns_true():
    """A wait in progress aborts when the generation is bumped mid-wait."""
    interrupter = threading.Timer(0.1, interrupt_pending_retries)
    interrupter.start()
    try:
        start = time.monotonic()
        aborted = backoff_wait(10.0)
        elapsed = time.monotonic() - start
    finally:
        interrupter.join()

    assert aborted is True
    assert elapsed < 2.0


def test_backoff_wait_stale_generation_returns_immediately():
    """A generation captured before an interrupt is permanently stale."""
    gen = current_generation()
    interrupt_pending_retries()

    start = time.monotonic()
    aborted = backoff_wait(10.0, gen)
    elapsed = time.monotonic() - start

    assert aborted is True
    assert elapsed < 1.0


def test_leaked_thread_bound_generation_aborts_post_teardown_backoff():
    """A thread bound before "teardown" aborts backoffs started after it.

    Simulates the leaked-subagent case: the worker binds its generation at
    thread birth, but is still in pre-LLM setup when the owning test's
    teardown bumps the generation. Its later backoff_wait (no explicit
    generation — the call captured a fresh, post-teardown generation) must
    still abort via the stale thread-bound generation.
    """
    ready = threading.Event()
    proceed = threading.Event()
    results: list[tuple[bool, float]] = []

    def worker():
        bind_thread_generation()  # thread birth, during the owning "test"
        ready.set()
        proceed.wait(timeout=5.0)  # pre-LLM setup outlives the test
        start = time.monotonic()
        aborted = backoff_wait(10.0)  # LLM call begins post-teardown
        results.append((aborted, time.monotonic() - start))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert ready.wait(timeout=5.0)

    interrupt_pending_retries()  # the owning test's teardown
    proceed.set()
    t.join(timeout=5.0)
    assert not t.is_alive()

    assert len(results) == 1
    aborted, elapsed = results[0]
    assert aborted is True
    assert elapsed < 2.0


def test_openai_handler_stale_generation_reraises_same_error(monkeypatch):
    """With a stale generation, the handler must re-raise immediately, not sleep."""
    from openai import APIConnectionError

    from gptme.llm.llm_openai import _handle_openai_transient_error

    # Ensure the env-based test short-circuit doesn't fire before the real sleep path.
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

    mock_request = MagicMock()
    error = APIConnectionError(message="Connection error.", request=mock_request)

    gen = current_generation()
    interrupt_pending_retries()

    start = time.monotonic()
    with pytest.raises(APIConnectionError) as exc_info:
        _handle_openai_transient_error(
            error, attempt=0, max_retries=5, base_delay=30.0, generation=gen
        )
    elapsed = time.monotonic() - start

    assert exc_info.value is error
    assert elapsed < 2.0


def test_interrupt_thread_aborts_target_thread():
    """interrupt_thread() aborts only the targeted thread's backoff wait."""
    ready = threading.Event()
    results: list[tuple[bool, float]] = []

    def worker():
        bind_thread_generation()
        ready.set()
        start = time.monotonic()
        aborted = backoff_wait(10.0)
        results.append((aborted, time.monotonic() - start))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert ready.wait(timeout=5.0)

    interrupt_thread(t)
    t.join(timeout=5.0)
    assert not t.is_alive()

    assert len(results) == 1
    aborted, elapsed = results[0]
    assert aborted is True
    assert elapsed < 2.0


def test_interrupt_thread_does_not_affect_other_threads():
    """interrupt_thread() leaves unrelated threads' backoffs untouched."""
    ready1 = threading.Event()
    ready2 = threading.Event()
    results1: list[bool] = []
    results2: list[bool] = []

    def worker1():
        bind_thread_generation()
        ready1.set()
        results1.append(backoff_wait(10.0))

    def worker2():
        bind_thread_generation()
        ready2.set()
        results2.append(backoff_wait(0.1))  # short wait, completes naturally

    t1 = threading.Thread(target=worker1, daemon=True)
    t2 = threading.Thread(target=worker2, daemon=True)
    t1.start()
    t2.start()
    assert ready1.wait(timeout=5.0)
    assert ready2.wait(timeout=5.0)

    interrupt_thread(t1)  # only interrupt t1
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not t1.is_alive()
    assert not t2.is_alive()

    assert results1 == [True]  # t1 was interrupted
    assert results2 == [False]  # t2 completed its short wait naturally


def test_interrupt_thread_before_bind_still_aborts():
    """interrupt_thread before bind_thread_generation still interrupts the worker.

    Simulates the startup race: teardown fires while the thread is in early
    setup, before it has called bind_thread_generation(). The pre-signaled
    event path ensures the thread still aborts at its next backoff_wait().
    """
    started = threading.Event()
    proceed = threading.Event()
    results: list[bool] = []

    def worker():
        started.set()
        proceed.wait(timeout=5.0)  # simulates pre-bind setup time
        bind_thread_generation()  # called after interrupt_thread()
        results.append(backoff_wait(10.0))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert started.wait(timeout=5.0)

    # Interrupt BEFORE the thread calls bind_thread_generation()
    interrupt_thread(t)
    proceed.set()
    t.join(timeout=5.0)
    assert not t.is_alive()

    assert results == [True]  # still interrupted despite startup race


def test_release_thread_clears_stale_event_for_ident_reuse():
    """release_thread() prevents a stale signaled event from poisoning a later bind.

    Thread ident reuse scenario: Thread A is interrupted and exits. If it
    calls release_thread() before dying, a new thread that gets Thread A's ident
    starts with a fresh, unset event. Without release_thread(), bind_thread_generation()
    adopts the stale signaled event and aborts at its first backoff.

    We simulate this in a single thread: interrupt → release → rebind. The
    second backoff should complete naturally (not abort) because release_thread()
    cleared the stale event from the registry.
    """
    interrupted = threading.Event()
    released = threading.Event()
    rebound = threading.Event()
    results: list[bool] = []

    def worker():
        bind_thread_generation()
        interrupted.wait(timeout=5.0)

        # Simulates thread exit cleanup: release removes the signaled event.
        release_thread()
        released.set()

        # Simulates a new thread starting with the same ident (rebind with no
        # pre-signaled event in the registry — should get a fresh, clean event).
        bind_thread_generation()
        rebound.set()

        # This backoff must complete naturally; NOT abort due to the stale event.
        results.append(backoff_wait(0.1))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    # Let worker reach first bind_thread_generation before we interrupt.
    # (There's no explicit "bound" gate here; interrupt_thread is safe to call early
    # due to the startup-race pre-signaling path, but for clarity we signal after start.)
    t.join(timeout=0)  # yield to let the thread start
    interrupt_thread(t)  # signal the event
    interrupted.set()

    assert released.wait(timeout=5.0)
    assert rebound.wait(timeout=5.0)
    t.join(timeout=5.0)
    assert not t.is_alive()

    # After release + rebind, the short backoff must NOT abort.
    assert results == [False]


def test_anthropic_handler_stale_generation_reraises_same_error(monkeypatch):
    """Same as the OpenAI case, for the Anthropic transient-error handler."""
    from anthropic import APIStatusError

    from gptme.llm.llm_anthropic import _handle_anthropic_transient_error

    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)

    mock_response = MagicMock()
    mock_response.status_code = 500
    error = APIStatusError("Internal server error", response=mock_response, body=None)

    gen = current_generation()
    interrupt_pending_retries()

    start = time.monotonic()
    with pytest.raises(APIStatusError) as exc_info:
        _handle_anthropic_transient_error(
            error, attempt=0, max_retries=5, base_delay=30.0, generation=gen
        )
    elapsed = time.monotonic() - start

    assert exc_info.value is error
    assert elapsed < 2.0
