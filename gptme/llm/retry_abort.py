"""Interrupt signal for LLM retry backoff sleeps.

The retry decorators in llm_openai/llm_anthropic sleep through exponential
backoff (up to ~15s total) inside whatever thread issued the LLM request, so
threads stuck in backoff cannot be joined promptly. Each retrying call
captures the generation at call start; interrupt_pending_retries() bumps the
generation, making every backoff wait belonging to an older-generation call
return immediately so the handler re-raises the original transient error.
Production never calls interrupt_pending_retries(); test teardown uses it to
reap leaked subagent threads (a leaked thread's call always started before
its owning test's teardown, so its generation is always stale afterwards).
"""

import threading
import time

_generation = 0
_lock = threading.Lock()
_tls = threading.local()


def current_generation() -> int:
    """Capture the current retry generation (call at LLM-call start)."""
    return _generation


def bind_thread_generation() -> None:
    """Bind the current generation to this thread (call at thread-target entry).

    Subagent worker threads call this first thing; every later backoff wait in
    the thread aborts once a teardown bumps the generation — even for LLM calls
    that only begin after the owning test finished (a call-start capture alone
    misses threads still in pre-LLM setup at teardown time).
    """
    _tls.generation = _generation


def interrupt_pending_retries() -> None:
    """Abort every retry backoff whose LLM call began before this point."""
    global _generation
    with _lock:
        _generation += 1


def backoff_wait(delay: float, generation: int | None = None) -> bool:
    """Wait up to ``delay`` seconds before a retry attempt.

    Returns True (abort the retry, re-raise) if interrupt_pending_retries()
    was called after the effective generation was captured. The effective
    generation is the OLDEST of the explicit/call generation and the thread
    generation bound via bind_thread_generation() (generations only grow, so
    ``min`` means "abort if either capture point is stale"). ``generation=None``
    captures the current generation at wait start (still aborts if interrupted
    mid-wait). Polls in 50ms steps — negligible for seconds-scale, rare
    backoff sleeps.
    """
    if generation is None:
        generation = _generation
    thread_generation = getattr(_tls, "generation", None)
    if thread_generation is not None:
        generation = min(generation, thread_generation)
    deadline = time.monotonic() + delay
    while True:
        if _generation != generation:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
