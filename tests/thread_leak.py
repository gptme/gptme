"""Thread-leak detection and dict-race diagnostics for the test suite.

Background
----------
`RuntimeError: dictionary changed size during iteration` has surfaced at pytest
setup/teardown across many unrelated test files.  The mechanism is always the
same: a background thread outlives the test that started it and later mutates a
process-global dict (usually ``sys.modules`` via a lazy import) while the main
thread iterates it.

#3257 fixed this for *registered subagent* threads.  gptme starts threads from
~27 other sites (server, ACP, computer transport, shell, hooks, oauth, sound,
tokens); any of those can reproduce the same race.  This module provides the
class-level tools:

* :func:`diff_threads` — name the threads a test left running.
* :func:`format_thread_stacks` — dump every live thread's stack, so the *next*
  CI occurrence names the mutating thread instead of showing a single truncated
  ``contextlib.__enter__`` frame.
"""

import sys
import threading
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import FrameType
from typing import Protocol

__all__ = [
    "DICT_RACE_MESSAGE",
    "LeakedThread",
    "diff_threads",
    "format_leaks",
    "format_thread_stacks",
    "is_dict_iteration_race",
    "snapshot_threads",
]

#: The exact CPython message for the race this module exists to diagnose.
DICT_RACE_MESSAGE = "dictionary changed size during iteration"

#: Threads that legitimately live for the whole worker process.  Leaks matching
#: these names are noise, not evidence.
_IGNORED_THREAD_NAMES = frozenset(
    {
        "MainThread",
        # pytest-timeout's per-item watchdog
        "pytest-timeout thread",
    }
)

#: Thread-name prefixes that produce *soft* warnings instead of hard failures.
#:
#: ``ThreadPoolExecutor-`` and ``asyncio_`` workers are reported in the terminal
#: leak summary so per-test executor leaks remain visible, but they are never
#: failed by ``GPTME_STRICT_THREAD_LEAKS=1``.  The reason: lazily initialised
#: process-lifetime executors (e.g. a cached regex or I/O pool) start *after* the
#: per-test snapshot, making them look like leaks for the first test that triggers
#: them even though they persist intentionally.  The before-snapshot suppresses
#: these workers for every *subsequent* test (they're in the snapshot by then), so
#: they do not produce noise across the suite — only the first-trigger test sees
#: the warning, which is still useful signal worth reviewing.
_SOFT_WARN_THREAD_PREFIXES = (
    "ThreadPoolExecutor-",
    "asyncio_",
)


class _ThreadLike(Protocol):
    """Structural view of the parts of ``threading.Thread`` we inspect."""

    name: str
    ident: int | None
    daemon: bool

    def is_alive(self) -> bool: ...


@dataclass
class LeakedThread:
    """A thread that was started during a test and outlived its teardown."""

    name: str
    ident: int | None
    daemon: bool
    stack: str = field(default="", repr=False)
    #: If True, this thread is visible in the terminal summary but is never
    #: failed by ``GPTME_STRICT_THREAD_LEAKS=1``.  Used for executor workers
    #: whose lazy initialisation makes them look like per-test leaks.
    soft: bool = False


def _is_ignored(name: str) -> bool:
    return name in _IGNORED_THREAD_NAMES


def _is_soft_warned(name: str) -> bool:
    """True for threads that are reported but never failed in strict mode."""
    return name.startswith(_SOFT_WARN_THREAD_PREFIXES)


def snapshot_threads() -> frozenset[tuple[int, int]]:
    """Return (ident, object-id) pairs for all currently alive threads.

    Pairing the ident with ``id(thread)`` catches the ident-reuse case: CPython
    thread identifiers are unique among *living* threads but may be reused once a
    thread exits.  A replacement thread has a different object id even when it
    inherits the same integer ident.
    """
    return frozenset(
        (t.ident, id(t)) for t in threading.enumerate() if t.ident is not None
    )


def diff_threads(
    before: frozenset[tuple[int, int]],
    *,
    threads: Sequence[_ThreadLike] | None = None,
    frames: Mapping[int, FrameType] | None = None,
) -> list[LeakedThread]:
    """Return threads alive now that were not alive in ``before``.

    ``threads``/``frames`` are injectable for testing; by default the live
    interpreter state is used.
    """
    live = threading.enumerate() if threads is None else threads
    if frames is None:
        frames = dict(sys._current_frames())

    leaked: list[LeakedThread] = []
    for thread in live:
        ident = thread.ident
        if ident is None:
            continue
        if (ident, id(thread)) in before:
            continue
        if not thread.is_alive():
            continue
        if _is_ignored(thread.name):
            continue
        frame = frames.get(ident)
        stack = "".join(traceback.format_stack(frame)) if frame is not None else ""
        leaked.append(
            LeakedThread(
                name=thread.name,
                ident=ident,
                daemon=bool(thread.daemon),
                stack=stack,
                soft=_is_soft_warned(thread.name),
            )
        )
    return leaked


def format_leaks(nodeid: str, leaks: list[LeakedThread]) -> str:
    """Render a leak report naming the test and each surviving thread."""
    lines = [
        (
            f"THREAD LEAK: {nodeid} left {len(leaks)} thread(s) running past "
            f"teardown. A leaked thread that lazy-imports can race main-thread "
            f"dict iteration ({DICT_RACE_MESSAGE!r}) in an unrelated test file."
        )
    ]
    for leak in leaks:
        lines.append(f"  - {leak.name} (ident={leak.ident}, daemon={leak.daemon})")
        if leak.stack:
            lines.extend(f"      {line}" for line in leak.stack.rstrip().splitlines())
    return "\n".join(lines)


def is_dict_iteration_race(exc: BaseException | None) -> bool:
    """True if ``exc`` is the dict-mutated-during-iteration race."""
    return isinstance(exc, RuntimeError) and DICT_RACE_MESSAGE in str(exc)


def format_thread_stacks(
    *,
    threads: Sequence[_ThreadLike] | None = None,
    frames: Mapping[int, FrameType] | None = None,
) -> str:
    """Dump every live thread with its stack.

    Used when the race fires: CI only shows a single truncated
    ``contextlib.__enter__`` frame, which names neither the generator that was
    iterating nor the thread that mutated the dict underneath it.
    """
    live = threading.enumerate() if threads is None else threads
    if frames is None:
        frames = dict(sys._current_frames())

    lines = [f"LIVE THREADS ({len(live)}):"]
    for thread in live:
        lines.append(
            f"  --- {thread.name} (ident={thread.ident}, daemon={thread.daemon}, "
            f"alive={thread.is_alive()})"
        )
        frame = frames.get(thread.ident) if thread.ident is not None else None
        if frame is None:
            lines.append("      <no frame captured>")
            continue
        stack = "".join(traceback.format_stack(frame))
        lines.extend(f"      {line}" for line in stack.rstrip().splitlines())
    return "\n".join(lines)
