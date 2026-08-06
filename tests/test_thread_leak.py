"""Tests for the thread-leak detector helpers (tests/thread_leak.py)."""

import sys
import threading

from thread_leak import (
    diff_threads,
    format_leaks,
    format_thread_stacks,
    is_dict_iteration_race,
    snapshot_threads,
)


class FakeThread:
    def __init__(self, name, ident, daemon=True, alive=True):
        self.name = name
        self.ident = ident
        self.daemon = daemon
        self._alive = alive

    def is_alive(self):
        return self._alive


def _frame():
    return sys._getframe()


def test_snapshot_includes_main_thread():
    assert threading.current_thread().ident in snapshot_threads()


def test_diff_reports_only_new_live_threads():
    before = {1}
    threads = [
        FakeThread("old", 1),
        FakeThread("new-leaker", 2),
        FakeThread("finished", 3, alive=False),
    ]
    leaks = diff_threads(before, threads=threads, frames={})
    assert [leak.name for leak in leaks] == ["new-leaker"]
    assert leaks[0].ident == 2
    assert leaks[0].daemon is True


def test_diff_ignores_known_long_lived_threads():
    threads = [
        FakeThread("MainThread", 1, daemon=False),
        FakeThread("pytest-timeout thread", 2),
        FakeThread("ThreadPoolExecutor-0_1", 3),
        FakeThread("asyncio_0", 4),
    ]
    assert diff_threads(set(), threads=threads, frames={}) == []


def test_diff_captures_stack_when_frame_available():
    threads = [FakeThread("leaker", 7)]
    leaks = diff_threads(set(), threads=threads, frames={7: _frame()})
    assert "test_diff_captures_stack_when_frame_available" in leaks[0].stack


def test_diff_tolerates_missing_frame():
    leaks = diff_threads(set(), threads=[FakeThread("leaker", 7)], frames={})
    assert leaks[0].stack == ""


def test_format_leaks_names_test_and_threads():
    leaks = diff_threads(set(), threads=[FakeThread("acp-close", 9)], frames={})
    out = format_leaks("tests/test_x.py::test_y", leaks)
    assert "tests/test_x.py::test_y" in out
    assert "acp-close" in out
    assert "ident=9" in out


def test_is_dict_iteration_race():
    assert is_dict_iteration_race(
        RuntimeError("dictionary changed size during iteration")
    )
    assert not is_dict_iteration_race(RuntimeError("something else"))
    assert not is_dict_iteration_race(ValueError("dictionary changed size"))
    assert not is_dict_iteration_race(None)


def test_format_thread_stacks_lists_threads_and_frames():
    threads = [FakeThread("worker-1", 11), FakeThread("worker-2", 12)]
    out = format_thread_stacks(threads=threads, frames={11: _frame()})
    assert "LIVE THREADS (2):" in out
    assert "worker-1" in out and "worker-2" in out
    assert "<no frame captured>" in out  # worker-2 has none
    assert "test_format_thread_stacks_lists_threads_and_frames" in out


def test_detector_sees_a_real_leaked_thread():
    """End-to-end: a thread started after the snapshot is reported."""
    stop = threading.Event()
    before = snapshot_threads()
    t = threading.Thread(target=stop.wait, name="deliberate-leaker", daemon=True)
    t.start()
    try:
        names = [leak.name for leak in diff_threads(before)]
        assert "deliberate-leaker" in names
    finally:
        stop.set()
        t.join(timeout=5)
    assert "deliberate-leaker" not in [leak.name for leak in diff_threads(before)]
