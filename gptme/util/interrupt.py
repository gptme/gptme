"""
Sets up a KeyboardInterrupt handler to handle Ctrl-C during the chat loop.
"""

import os
import threading
import time
from contextvars import ContextVar
from typing import SupportsIndex

from . import console

# Use ContextVar for thread/session isolation
# In server/multi-session scenarios, each session has its own interrupt state
_interruptible_var: ContextVar[bool] = ContextVar("interruptible", default=False)
_last_interrupt_time_var: ContextVar[float] = ContextVar(
    "last_interrupt_time", default=0.0
)


def handle_keyboard_interrupt(signum, frame):  # pragma: no cover
    """
    This handler allows interruption of the assistant or tool execution when in an interruptible state,
    while still providing a safeguard against accidental exits during user input.
    """
    # Don't raise in non-main threads — causes "Exception ignored during
    # thread shutdown" errors, especially on Windows where worker threads
    # use threading-based I/O instead of select().
    if threading.current_thread() is not threading.main_thread():
        return

    current_time = time.time()

    # if testing with pytest
    testing = bool(os.getenv("PYTEST_CURRENT_TEST"))

    if _interruptible_var.get() or testing:
        raise KeyboardInterrupt

    _last_interrupt_time_var.set(current_time)
    console.print()
    console.log("Interrupted. Press Ctrl-D to exit.")


def set_interruptible():
    """Set the interruptible flag for the current context/session."""
    _interruptible_var.set(True)


def clear_interruptible():
    """Clear the interruptible flag for the current context/session."""
    _interruptible_var.set(False)


# --- Interrupting a tool on another thread -----------------------------------
#
# Tools run on whatever thread runs the chat loop. In the TUI that is a worker
# thread, which Ctrl+C (a key press in raw mode, not SIGINT) and signal-based
# timeouts can't reach. These helpers interrupt such a thread: the exception
# lands at its next Python bytecode, so a blocking C call (time.sleep, a
# subprocess wait, a socket read) delays it. Killing the processes the tool
# started unblocks a subprocess wait; interruptible_sleep() covers time.sleep.


def raise_in_thread(thread_id: int, exc: type[BaseException]) -> bool:
    """Raise ``exc`` in another thread at its next bytecode."""
    import ctypes

    return (
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_id), ctypes.py_object(exc)
        )
        == 1
    )


def clear_pending(thread_id: int) -> None:
    """Drop an exception raised with raise_in_thread() that hasn't landed yet."""
    import ctypes

    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)


def own_group_children() -> set[int]:
    """This process's direct children in its own process group.

    Persistent shells and background jobs run in their own sessions, so this
    is what tool code spawned directly (e.g. ``subprocess.run`` in ipython).
    """
    if os.name != "posix":
        return set()
    from ..tools.shell import _child_pids

    group = os.getpgrp()
    children = set()
    for pid in _child_pids(os.getpid()):
        try:
            if os.getpgid(pid) == group:
                children.add(pid)
        except ProcessLookupError:
            pass
    return children


def kill_new_children(before: set[int]) -> None:
    """Kill children started since ``before`` was taken, and their descendants."""
    import signal

    from ..tools.shell import _kill_descendants

    for pid in own_group_children() - before:
        _kill_descendants(pid)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                break
            time.sleep(0.1)


_real_sleep = time.sleep


def _sliced_sleep(secs: "float | SupportsIndex") -> None:
    """time.sleep that an exception raised from another thread can stop."""
    secs = float(secs)
    if secs < 0:
        raise ValueError("sleep length must be non-negative")
    end = time.monotonic() + secs
    while (left := end - time.monotonic()) > 0:
        _real_sleep(min(left, 0.1))


class interruptible_sleep:
    """Within this block, ``time.sleep`` can be interrupted from another
    thread (it sleeps in short slices). Process-wide while active; nests."""

    _depth = 0
    _lock = threading.Lock()

    def __enter__(self) -> None:
        with self._lock:
            type(self)._depth += 1
            time.sleep = _sliced_sleep

    def __exit__(self, *exc: object) -> None:
        with self._lock:
            type(self)._depth -= 1
            if not self._depth:
                time.sleep = _real_sleep
