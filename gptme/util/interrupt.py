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


_tracking: dict[int, list[set[int]]] = {}  # thread id -> stack of pid sets
_tracking_lock = threading.Lock()
_popen_hooked = False


def _hook_popen() -> None:
    """Record the pid of each subprocess.Popen started on a tracking thread."""
    global _popen_hooked
    import subprocess

    with _tracking_lock:
        if _popen_hooked:
            return
        _popen_hooked = True
    original = subprocess.Popen.__init__

    def __init__(self, *args, **kwargs) -> None:
        original(self, *args, **kwargs)
        with _tracking_lock:
            for pids in _tracking.get(threading.get_ident(), ()):
                pids.add(self.pid)

    subprocess.Popen.__init__ = __init__  # type: ignore[method-assign]


def start_tracking() -> set[int]:
    """Start recording processes this thread starts (via subprocess); the
    returned set fills as they start. Nests; end with stop_tracking()."""
    _hook_popen()
    pids: set[int] = set()
    with _tracking_lock:
        _tracking.setdefault(threading.get_ident(), []).append(pids)
    return pids


def stop_tracking(pids: set[int]) -> None:
    with _tracking_lock:
        stack = _tracking.get(threading.get_ident(), [])
        # by identity: nested sets are often equal (e.g. both empty), and
        # removing an outer one would stop it from recording new processes
        for i in reversed(range(len(stack))):
            if stack[i] is pids:
                del stack[i]
                break
        if not stack:
            _tracking.pop(threading.get_ident(), None)


def kill_processes(pids: set[int]) -> None:
    """Kill tracked processes and their descendants: SIGTERM, then SIGKILL.

    Only those in this process's group: persistent shells and background
    jobs run in their own sessions and are managed separately. The tree is
    listed once, so workers re-parented after SIGTERM are still killed.
    """
    import signal

    from ..tools.shell import _descendant_pids

    group = os.getpgrp()
    targets: list[int] = []
    for pid in pids:
        try:
            if os.getpgid(pid) != group:
                continue
        except ProcessLookupError:
            continue
        targets += [*_descendant_pids(pid), pid]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in targets:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        if sig == signal.SIGTERM and targets:
            time.sleep(0.2)


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
