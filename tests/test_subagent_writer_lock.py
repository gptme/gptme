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
        "f = open(sys.argv[1], 'r+b'); "
        "fcntl.flock(f, fcntl.LOCK_EX); "
        "print('locked', flush=True); "
        "time.sleep(60)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
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


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_launcher_rejects_busy_lease_without_starting_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A losing parent must not queue a second writer behind the active child."""
    import fcntl

    from gptme.tools.subagent import execution

    lock_path = tmp_path / WRITER_LOCK_FILENAME
    with lock_path.open("w+b") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        monkeypatch.setattr(execution.tempfile, "tempdir", str(tmp_path))
        monkeypatch.setattr(
            execution.subprocess,
            "Popen",
            lambda *a, **kw: pytest.fail("busy lease must not launch a child"),
        )
        # Bound the old blocking implementation so the regression fails instead
        # of hanging pytest. The fixed implementation returns before release.
        release = threading.Timer(1, lambda: fcntl.flock(holder, fcntl.LOCK_UN))
        release.start()
        try:
            with pytest.raises(BlockingIOError):
                execution._run_subagent_subprocess(
                    "continue", tmp_path, None, tmp_path, resume=True
                )
            assert not list(tmp_path.glob("gptme-prompt-*.txt"))
        finally:
            release.cancel()
            release.join()


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_real_launcher_hands_lease_to_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise real Popen/pass_fds, without inference or a child-side flock."""
    from gptme.tools.subagent import execution

    ready = tmp_path / "ready"
    release = tmp_path / "release"
    real_popen = subprocess.Popen
    script = (
        "import pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).touch(); "
        "\nwhile not pathlib.Path(sys.argv[2]).exists(): time.sleep(0.01)"
    )

    def launch_stub(cmd: list[str], **kwargs) -> subprocess.Popen:
        return real_popen(
            [sys.executable, "-c", script, str(ready), str(release)], **kwargs
        )

    monkeypatch.setattr(execution.subprocess, "Popen", launch_stub)
    proc = execution._run_subagent_subprocess(
        "continue", tmp_path, None, tmp_path, resume=True
    )
    try:
        deadline = time.monotonic() + 5
        while (
            not ready.exists() and proc.poll() is None and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert ready.exists(), "child did not reach readiness before exit/deadline"
        # Launcher has returned and closed its own descriptor. Only the child's
        # inherited fd can keep this lease held.
        assert _make_sa(tmp_path).is_running()
        release.touch()
        assert proc.wait(timeout=5) == 0
        assert not _make_sa(tmp_path).is_running()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_launcher_failure_releases_lease_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popen failure must not strand the lease or the temporary prompt."""
    from gptme.tools.subagent import execution

    monkeypatch.setattr(execution.tempfile, "tempdir", str(tmp_path))

    def fail_launch(*args, **kwargs) -> subprocess.Popen:
        raise OSError("launch failed")

    monkeypatch.setattr(execution.subprocess, "Popen", fail_launch)
    with pytest.raises(OSError, match="launch failed"):
        execution._run_subagent_subprocess(
            "continue", tmp_path, None, tmp_path, resume=True
        )
    assert not _make_sa(tmp_path).is_running()
    assert not list(tmp_path.glob("gptme-prompt-*.txt"))


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_lease_acquire_and_probe_are_serialized_by_gate(tmp_path: Path) -> None:
    """A liveness probe must never overlap a lease acquisition.

    Otherwise the probe's transient flock reads as a competing writer and
    rejects a valid continuation. Both go through the gate, so while the gate is
    held neither completes; after release both succeed with no writer present.
    """
    import fcntl

    from gptme.tools.subagent import execution

    (tmp_path / WRITER_LOCK_FILENAME).touch()
    results: dict[str, object] = {}

    def probe() -> None:
        results["probe"] = execution.writer_lease_held(tmp_path)

    def acquire() -> None:
        results["lease"] = execution.acquire_writer_lease(tmp_path)

    with (tmp_path / execution.WRITER_GATE_FILENAME).open("a+b") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX)
        threads = [threading.Thread(target=probe), threading.Thread(target=acquire)]
        for t in threads:
            t.start()
        time.sleep(0.2)
        assert results == {}, "probe/acquire must wait on the gate"
        fcntl.flock(gate, fcntl.LOCK_UN)
        for t in threads:
            t.join(timeout=5)
    lease = results["lease"]
    assert lease is not None
    lease.close()  # type: ignore[attr-defined]
    # The acquirer was never rejected by the probe (it would have raised
    # BlockingIOError and left no "lease" result). A probe that ran after the
    # acquisition legitimately reports the now-real writer.
    assert "probe" in results


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl not available on Windows")
def test_continue_rejected_by_busy_lease_leaves_active_attempt_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected continuation must not publish metadata or a failure outcome."""
    import fcntl

    from gptme.tools.subagent import api, execution
    from gptme.tools.subagent.types import ReturnType

    (tmp_path / "conversation.jsonl").write_text("")
    sa = _make_sa(tmp_path)
    with api._subagents_lock:
        api._subagents.append(sa)
    with api._subagent_results_lock:
        api._subagent_results[sa.agent_id] = ReturnType("success", "done")
    persisted: list[object] = []
    monkeypatch.setattr(api, "persist_subagent_meta", persisted.append)
    monkeypatch.setattr(
        execution,
        "_run_subagent_subprocess",
        lambda *a, **kw: pytest.fail("busy lease must not launch a child"),
    )
    try:
        # Simulate another parent's child (a live holder) appearing right after
        # this caller's is_running() probe: the lease acquisition must refuse.
        holder = (tmp_path / WRITER_LOCK_FILENAME).open("a+b")
        fcntl.flock(holder, fcntl.LOCK_EX)
        monkeypatch.setattr(Subagent, "is_running", lambda self: False)
        with pytest.raises(ValueError, match="still running"):
            api.subagent_continue(sa.agent_id, "again")
        assert persisted == []
        with api._subagents_lock:
            assert api._subagents == [sa]
        with api._subagent_results_lock:
            assert api._subagent_results[sa.agent_id] == ReturnType("success", "done")
    finally:
        holder.close()
        with api._subagents_lock:
            api._subagents[:] = [s for s in api._subagents if s is not sa]
        with api._subagent_results_lock:
            api._subagent_results.pop(sa.agent_id, None)
