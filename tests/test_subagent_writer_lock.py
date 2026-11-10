"""Tests for the subprocess writer-lease mechanism.

The writer lock (subagent-writer.lock) is held exclusively by the child process
for its entire lifetime via an inherited file descriptor. A surviving child after
a parent crash keeps the kernel flock until it exits, allowing is_running() to
detect liveness cross-restart without a stale PID file.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from gptme.tools.subagent.execution import WRITER_LOCK_FILENAME
from gptme.tools.subagent.types import Subagent


def _make_sa(logdir: Path, *, process=None) -> Subagent:
    """Minimal Subagent in subprocess mode, no live process reference (rehydrated)."""
    return Subagent(
        agent_id="lock-test",
        prompt="test",
        thread=None,
        logdir=logdir,
        model=None,
        execution_mode="subprocess",
        process=process,
    )


def _child_holding_lock(
    lock_path: Path, ready_event: threading.Event
) -> subprocess.Popen:
    """Launch a subprocess that holds LOCK_EX on lock_path until killed."""
    # The child opens the lock file and holds LOCK_EX until it exits.
    script = (
        "import fcntl, time, sys; "
        f"f = open('{lock_path}', 'r+b'); "
        "fcntl.flock(f, fcntl.LOCK_EX); "
        "print('locked', flush=True); "
        "time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    # Wait until the child reports it has the lock.
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert b"locked" in line, f"child did not acquire lock: {line!r}"
    return proc


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_is_running_returns_true_while_child_holds_lock(tmp_path):
    """is_running() must return True when the lock file is held by another process."""
    lock_path = tmp_path / WRITER_LOCK_FILENAME
    lock_path.touch()

    proc = _child_holding_lock(lock_path, threading.Event())
    try:
        sa = _make_sa(tmp_path)
        assert sa.is_running() is True, (
            "expected is_running=True while child holds lock"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_is_running_returns_false_after_child_exits(tmp_path):
    """is_running() must return False once the lock-holding child exits."""
    lock_path = tmp_path / WRITER_LOCK_FILENAME
    lock_path.touch()

    proc = _child_holding_lock(lock_path, threading.Event())
    proc.terminate()
    proc.wait(timeout=5)

    # Give the kernel a moment to release the flock (should be immediate).
    deadline = time.monotonic() + 1.0
    sa = _make_sa(tmp_path)
    while time.monotonic() < deadline:
        if not sa.is_running():
            break
        time.sleep(0.01)
    assert sa.is_running() is False, "expected is_running=False after child exited"


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_is_running_returns_false_when_lock_file_absent(tmp_path):
    """No lock file → subagent was spawned before the lease mechanism; not running."""
    sa = _make_sa(tmp_path)
    assert sa.is_running() is False


def test_is_running_uses_process_poll_when_process_present(tmp_path):
    """When a live process ref is available, poll() takes precedence over the lock file."""
    from unittest.mock import MagicMock

    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # still running

    # No lock file — but process ref wins.
    sa = _make_sa(tmp_path, process=mock_proc)
    assert sa.is_running() is True

    mock_proc.poll.return_value = 0  # exited
    assert sa.is_running() is False
