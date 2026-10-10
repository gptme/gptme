"""Subagent SIGKILL escalation also kills the child's persistent shells.

Persistent shells run in their own session, so killing the CLI alone leaves
them (and anything they detached) running. See gptme/gptme#4089.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from gptme.tools.subagent.execution import (
    _kill_recorded_shell_groups,
    _process_start_marker,
    _terminate_subprocess,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

_HAS_PROCFS = Path("/proc").exists()


def _recorded_entry(pid: int) -> str:
    """Mimic what a persistent shell writes to the pgid file."""
    marker = _process_start_marker(pid)
    return f"{pid} {marker}\n" if marker is not None else f"{pid}\n"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(pid, 0); treat it as dead when procfs can
    # tell us. Platforms without /proc (macOS) fall back to the signal probe.
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return True
    return stat.read_text().split()[2] != "Z"


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_sigkill_escalation_kills_recorded_shell_and_detached_child(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    pid_file = tmp_path / "detached.pid"
    # Stand-in for a CLI that ignores SIGTERM. Started before the shell, as in
    # production: the recorded group must postdate the CLI to be killed.
    cli = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)",
        ],
    )
    # Stand-in for a persistent shell: own session, detaches a long sleep.
    shell = subprocess.Popen(
        ["bash", "-c", f"sleep 300 & echo $! > {pid_file}; wait"],
        start_new_session=True,
    )
    # As in production, the shell records its pgid plus a start marker, so the
    # parent can verify the identity before signalling (also on macOS, where
    # the marker comes from `ps` rather than procfs).
    pgid_file.write_text(_recorded_entry(shell.pid))
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.05)
    detached = int(pid_file.read_text())
    time.sleep(0.2)  # let the CLI install its SIGTERM handler

    _terminate_subprocess(cli, pgid_file)

    assert cli.returncode is not None
    assert _wait_dead(detached), "detached grandchild survived the escalation"
    shell.wait(timeout=5)


@pytest.mark.skipif(
    not _HAS_PROCFS, reason="dead-leader member scan requires procfs (Linux)"
)
def test_kill_recorded_groups_reaches_group_with_dead_leader(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    pid_file = tmp_path / "detached.pid"
    # Session leader exits at once, leaving `sleep` in its leaderless group.
    leader = subprocess.Popen(
        ["bash", "-c", f"sleep 300 & echo $! > {pid_file}"],
        start_new_session=True,
    )
    leader.wait(timeout=5)
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        time.sleep(0.05)
    detached = int(pid_file.read_text())
    pgid_file.write_text(f"{leader.pid}\n")

    # The production caller passes the CLI's start time; with the leader gone
    # the group is accepted only because every member postdates it.
    _kill_recorded_shell_groups(pgid_file, after_ticks=1)

    assert _wait_dead(detached), "group with a dead leader survived the kill"


def test_kill_recorded_groups_skips_when_start_cannot_be_verified(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    # macOS has no procfs: neither the recorded entry nor the CLI carries a
    # start time, so a pid alone is unverifiable. A live unrelated session
    # leader that reused a dead shell's pid must not be signalled.
    leader = subprocess.Popen(["bash", "-c", "sleep 60"], start_new_session=True)
    try:
        # Wait for the child's setsid() so it is a session leader by the time
        # the kill path checks it (otherwise the group lookup races).
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and os.getsid(leader.pid) != leader.pid:
            time.sleep(0.01)
        pgid_file.write_text(f"{leader.pid}\n")
        _kill_recorded_shell_groups(pgid_file, after_ticks=None)
        # SIGKILL delivery is asynchronous: give it a moment before deciding
        # the entry was left alone. poll() reaps a signalled child, so it
        # reports the kill that /proc liveness checks can miss.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and leader.poll() is None:
            time.sleep(0.02)
        assert leader.poll() is None, "an unverifiable entry was signalled"
    finally:
        leader.kill()
        leader.wait()


def test_kill_recorded_groups_skips_non_session_leaders_and_garbage(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    # Our own pid is not a session leader of its own group here, and junk
    # lines / pid 1 must be ignored rather than raising.
    pgid_file.write_text(f"not-a-pid\n1\n{os.getpid()}\n999999999\n")
    if os.getsid(os.getpid()) == os.getpid():
        pytest.skip("test runner is itself a session leader")
    _kill_recorded_shell_groups(pgid_file)  # must not kill us or raise
    assert _alive(os.getpid()), "the killing process's own group was signalled"
    _kill_recorded_shell_groups(tmp_path / "missing")


def test_kill_recorded_groups_skips_groups_predating_the_subagent(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    # A stale entry naming a group that started before the subagent must not
    # be signalled: a shell the child spawned cannot predate the child.
    leader = subprocess.Popen(["bash", "-c", "sleep 60"], start_new_session=True)
    try:
        pgid_file.write_text(f"{leader.pid}\n")
        _kill_recorded_shell_groups(pgid_file, after_ticks=1 << 62)
        assert _alive(leader.pid), "a group predating the subagent was killed"
    finally:
        leader.kill()
        leader.wait()


def test_shell_session_records_its_pgid(tmp_path: Path, monkeypatch):
    from gptme.tools.shell import _record_shell_pgid

    pgid_file = tmp_path / "shell-pgids"
    monkeypatch.setenv("GPTME_SHELL_PGID_FILE", str(pgid_file))
    _record_shell_pgid(os.getpid())
    _record_shell_pgid(os.getppid())
    pids = [line.split()[0] for line in pgid_file.read_text().splitlines()]
    assert pids == [str(os.getpid()), str(os.getppid())]

    monkeypatch.delenv("GPTME_SHELL_PGID_FILE")
    _record_shell_pgid(5555)  # no-op without the variable
    pids_recorded = [line.split()[0] for line in pgid_file.read_text().splitlines()]
    assert "5555" not in pids_recorded


def test_kill_recorded_groups_skips_a_reused_pid(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    # Live session leader, but the recorded start time is not its own: the pid
    # was recycled, so this group is not the recorded shell's.
    leader = subprocess.Popen(["bash", "-c", "sleep 60"], start_new_session=True)
    try:
        pgid_file.write_text(f"{leader.pid} 1\n")
        _kill_recorded_shell_groups(pgid_file)
        assert _alive(leader.pid), "a recycled pid's group was killed"
    finally:
        leader.kill()
        leader.wait()


def test_process_start_marker_is_stable_and_none_for_dead_pids():
    proc = subprocess.Popen(["bash", "-c", "sleep 30"], start_new_session=True)
    try:
        first = _process_start_marker(proc.pid)
        assert first is not None
        assert first == _process_start_marker(proc.pid)
    finally:
        proc.kill()
        proc.wait()
    assert _process_start_marker(proc.pid) is None
    assert _process_start_marker(999999999) is None


def test_process_start_marker_falls_back_to_ps(monkeypatch):
    import gptme.tools.subagent.execution as execution

    proc = subprocess.Popen(["bash", "-c", "sleep 30"], start_new_session=True)
    try:
        monkeypatch.setattr(execution, "_proc_start_ticks", lambda pid: None)
        marker = execution._process_start_marker(proc.pid)
        assert marker is not None
        assert marker == execution._process_start_marker(proc.pid)
    finally:
        proc.kill()
        proc.wait()


def test_kill_recorded_groups_uses_portable_marker_without_procfs(
    tmp_path: Path, monkeypatch
):
    """A live shell identified via `ps` (macOS path) is still reaped."""
    import gptme.tools.subagent.execution as execution

    pgid_file = tmp_path / "shell-pgids"
    shell = subprocess.Popen(["bash", "-c", "sleep 60"], start_new_session=True)
    try:
        monkeypatch.setattr(execution, "_proc_start_ticks", lambda pid: None)
        marker = execution._process_start_marker(shell.pid)
        assert marker is not None
        pgid_file.write_text(f"{shell.pid} {marker}\n")
        execution._kill_recorded_shell_groups(pgid_file)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and shell.poll() is None:
            time.sleep(0.02)
        assert shell.poll() is not None, "a portable-marker shell was not reaped"
    finally:
        if shell.poll() is None:
            shell.kill()
        shell.wait()
