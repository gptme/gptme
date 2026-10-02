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
    _terminate_subprocess,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


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
    # Stand-in for a persistent shell: own session, detaches a long sleep.
    shell = subprocess.Popen(
        ["bash", "-c", f"sleep 300 & echo $! > {pid_file}; wait"],
        start_new_session=True,
    )
    pgid_file.write_text(f"{shell.pid}\n")
    # Stand-in for a CLI that ignores SIGTERM.
    cli = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(300)",
        ],
    )
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

    _kill_recorded_shell_groups(pgid_file)

    assert _wait_dead(detached), "group with a dead leader survived the kill"


def test_kill_recorded_groups_skips_non_session_leaders_and_garbage(tmp_path: Path):
    pgid_file = tmp_path / "shell-pgids"
    # Our own pid is not a session leader of its own group here, and junk
    # lines / pid 1 must be ignored rather than raising.
    pgid_file.write_text(f"not-a-pid\n1\n{os.getpid()}\n999999999\n")
    if os.getsid(os.getpid()) == os.getpid():
        pytest.skip("test runner is itself a session leader")
    _kill_recorded_shell_groups(pgid_file)  # must not kill us or raise
    _kill_recorded_shell_groups(tmp_path / "missing")


def test_shell_session_records_its_pgid(tmp_path: Path, monkeypatch):
    from gptme.tools.shell import _record_shell_pgid

    pgid_file = tmp_path / "shell-pgids"
    monkeypatch.setenv("GPTME_SHELL_PGID_FILE", str(pgid_file))
    _record_shell_pgid(4242)
    _record_shell_pgid(4343)
    assert pgid_file.read_text().split() == ["4242", "4343"]

    monkeypatch.delenv("GPTME_SHELL_PGID_FILE")
    _record_shell_pgid(5555)  # no-op without the variable
    assert "5555" not in pgid_file.read_text()
