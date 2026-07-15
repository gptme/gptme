"""Tests for the retry-backoff interrupt signal (gptme/llm/retry_abort.py).

See retry_abort.py for the rationale: leaked subagent threads sleep through
LLM retry backoff (up to ~15s) far past the 2s conftest join timeout. The
generation counter lets test teardown make every backoff wait belonging to an
already-started LLM call return immediately — permanently, with no clear/reset
window for a leaked thread to slip through.
"""

import threading
import time
from unittest.mock import MagicMock

import pytest

from gptme.llm.retry_abort import (
    backoff_wait,
    bind_thread_generation,
    current_generation,
    interrupt_pending_retries,
)


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
