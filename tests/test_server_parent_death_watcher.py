"""Tests for the gptme-server parent-death watcher (gptme/gptme#2260)."""

import os
import signal
import threading
import time
from unittest.mock import patch

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

from gptme.server.cli import _pid_alive, _start_parent_death_watcher


def test_pid_alive_for_self():
    assert _pid_alive(os.getpid())


def test_pid_alive_returns_false_for_dead_pid():
    # PID 0 / very high never-allocated PIDs always look dead.
    # PID 1 is init/launchd, always alive on POSIX, so use a guaranteed-dead pid.
    assert not _pid_alive(2_147_483_646)


def test_watcher_skips_when_already_orphaned():
    # When watch_pid <= 1 we treat as already-orphaned and don't spawn the thread.
    threads_before = threading.active_count()
    _start_parent_death_watcher(watch_pid=1, poll_interval=0.01)
    _start_parent_death_watcher(watch_pid=0, poll_interval=0.01)
    # Give any (incorrectly-spawned) thread time to start.
    time.sleep(0.05)
    assert threading.active_count() == threads_before


def test_watcher_sends_sigterm_when_watched_pid_disappears():
    """When the watched PID is gone the watcher SIGTERMs the current process."""
    received: list[int] = []
    original = signal.getsignal(signal.SIGTERM)

    def _handler(signum, _frame):
        received.append(signum)

    signal.signal(signal.SIGTERM, _handler)
    try:
        # _pid_alive returns False immediately → watcher fires SIGTERM on the
        # first poll. Use a dead PID so we don't depend on real process state.
        with patch("gptme.server.cli._pid_alive", return_value=False):
            _start_parent_death_watcher(watch_pid=99_999_999, poll_interval=0.01)
            # Wait for the watcher to fire.
            for _ in range(50):
                if received:
                    break
                time.sleep(0.01)
        assert received == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)
