"""Interrupt signal for LLM retry backoff sleeps.

The retry decorators in llm_openai/llm_anthropic sleep through exponential
backoff (up to ~15s total) inside whatever thread issued the LLM request, so
threads stuck in backoff cannot be joined promptly. Each retrying call
captures the generation at call start; interrupt_pending_retries() bumps the
generation, making every backoff wait belonging to an older-generation call
return immediately so the handler re-raises the original transient error.
Production never calls either interrupt function; test teardown uses
interrupt_thread() on the specific registered subagent threads to reap leaked
threads without affecting unrelated LLM work in the same process.
"""

import threading
import time

_generation = 0
_lock = threading.Lock()
_tls = threading.local()

# Maps thread ident → per-thread interrupt event. Populated by bind_thread_generation();
# signalled by interrupt_thread(). Allows scoped interrupts that only affect specific
# registered subagent threads, not every LLM call in the process.
_thread_events: dict[int, "threading.Event"] = {}
_thread_events_lock = threading.Lock()


def current_generation() -> int:
    """Capture the current retry generation (call at LLM-call start)."""
    return _generation


def bind_thread_generation() -> None:
    """Bind the current generation and an interrupt event to this thread.

    Subagent worker threads call this first thing; every later backoff wait in
    the thread aborts once interrupt_thread() signals this thread's event — even
    for LLM calls that only begin after the owning test finished (a call-start
    capture alone misses threads still in pre-LLM setup at teardown time).
    """
    _tls.generation = _generation
    event = threading.Event()
    _tls.interrupt_event = event
    ident = threading.current_thread().ident
    if ident is not None:
        with _thread_events_lock:
            _thread_events[ident] = event


def release_thread() -> None:
    """Remove this thread from the interrupt event registry (call at thread exit)."""
    ident = threading.current_thread().ident
    if ident is not None:
        with _thread_events_lock:
            _thread_events.pop(ident, None)


def interrupt_thread(thread: threading.Thread) -> None:
    """Abort retry backoffs only for the given thread.

    Prefer this over interrupt_pending_retries() — it is scoped to one
    registered subagent thread and does not cancel unrelated LLM work in
    the same pytest worker.
    """
    ident = thread.ident
    if ident is None:
        return
    with _thread_events_lock:
        event = _thread_events.get(ident)
    if event is not None:
        event.set()


def interrupt_pending_retries() -> None:
    """Abort every retry backoff in the process.

    Process-wide: affects all threads, not just registered subagents. Use
    interrupt_thread() for scoped teardown instead.
    """
    global _generation
    with _lock:
        _generation += 1


def backoff_wait(delay: float, generation: int | None = None) -> bool:
    """Wait up to ``delay`` seconds before a retry attempt.

    Returns True (abort the retry, re-raise) if interrupt_thread() signalled
    this thread's event, or if interrupt_pending_retries() was called after the
    effective generation was captured. The effective generation is the OLDEST of
    the explicit/call generation and the thread generation bound via
    bind_thread_generation() (generations only grow, so ``min`` means "abort if
    either capture point is stale"). ``generation=None`` captures the current
    generation at wait start (still aborts if interrupted mid-wait). Polls in
    50ms steps — negligible for seconds-scale, rare backoff sleeps.
    """
    if generation is None:
        generation = _generation
    thread_generation = getattr(_tls, "generation", None)
    if thread_generation is not None:
        generation = min(generation, thread_generation)
    interrupt_event: threading.Event | None = getattr(_tls, "interrupt_event", None)
    if interrupt_event is not None and interrupt_event.is_set():
        return True
    deadline = time.monotonic() + delay
    while True:
        if _generation != generation:
            return True
        if interrupt_event is not None and interrupt_event.is_set():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.05, remaining))
