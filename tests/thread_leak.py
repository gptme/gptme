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

#: Prefixes for pooled/shared executors that are intentionally reused across
#: tests rather than torn down per test.
_IGNORED_THREAD_PREFIXES = (
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


def _is_ignored(name: str) -> bool:
    if name in _IGNORED_THREAD_NAMES:
        return True
    return name.startswith(_IGNORED_THREAD_PREFIXES)


def snapshot_threads() -> set[int]:
    """Return the idents of all threads currently alive."""
    return {t.ident for t in threading.enumerate() if t.ident is not None}


def diff_threads(
    before: set[int],
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
        if ident is None or ident in before:
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
