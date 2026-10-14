import os
import shlex
import sys
import threading
import time

import pytest

from gptme.tools import shell_memory
from gptme.tools.shell_memory import MemoryGuard, Target, group_rss, system_memory

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX only")

GB = 1024**3


def _target(pgid: int, killed: list[str], reasons: list[str]) -> Target:
    return Target(
        pgid=pgid,
        label=f"job {pgid}",
        kill=lambda: killed.append(f"job {pgid}"),
        alive=lambda: True,
        on_killed=reasons.append,
    )


def test_guard_kills_largest_group_only_when_memory_low():
    memory = {"value": (16 * GB, 0.50, False)}
    rss = {1: 3 * GB, 2: 6 * GB, 3: 100}
    guard = MemoryGuard(memory=lambda: memory["value"], rss=lambda: rss)
    killed: list[str] = []
    reasons: list[str] = []
    for pgid in rss:
        guard._targets[pgid] = _target(pgid, killed, reasons)

    assert guard.check() is None  # plenty available
    memory["value"] = (16 * GB, 0.04, False)
    assert guard.check() is guard._targets[2]
    assert killed == ["job 2"]
    assert "Killed job 2" in reasons[0] and "6.0 GB" in reasons[0]
    assert guard.check() is None  # cooldown: let memory recover first


def test_guard_acts_on_kernel_pressure_but_not_on_small_groups():
    rss = {1: 100 * 1024**2}  # 100 MB: not what's exhausting 16 GB
    guard = MemoryGuard(memory=lambda: (16 * GB, 0.30, True), rss=lambda: rss)
    killed: list[str] = []
    guard._targets[1] = _target(1, killed, [])
    assert guard.check() is None
    rss[1] = 2 * GB
    assert guard.check() is not None
    assert killed == ["job 1"]


def test_guard_forgets_finished_targets():
    guard = MemoryGuard(memory=lambda: (16 * GB, 0.01, True), rss=lambda: {1: GB})
    guard._targets[1] = Target(1, "done", lambda: None, lambda: False, lambda r: None)
    assert guard.check() is None
    assert guard._targets == {}


def test_system_memory_readings_are_sane():
    memory = system_memory()
    assert memory is not None
    total, available, _pressure = memory
    assert total > 256 * 1024**2
    assert 0 <= available <= 1
    assert os.getpgrp() in group_rss()


@pytest.fixture
def fake_low_memory(monkeypatch):
    """Install a guard that sees memory as exhausted and any watched group as huge."""
    rss: dict[int, int] = {}

    def fake_rss() -> dict[int, int]:
        return dict.fromkeys(guard._targets, 4 * GB) | rss

    guard = MemoryGuard(memory=lambda: (16 * GB, 0.02, True), rss=fake_rss)
    guard._thread = threading.current_thread()  # no polling thread: check() by hand
    monkeypatch.setattr(shell_memory, "_guard", guard)
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "1")
    return guard


def test_background_job_is_killed_and_told_why(fake_low_memory):
    from gptme.tools.shell_background import (
        _completion_message,
        reset_background_jobs,
        start_background_job,
    )

    job = start_background_job("sleep 30")
    try:
        assert fake_low_memory.check() is not None
        assert not job.is_running()
        message = _completion_message(job).content
        assert f"Killed background job #{job.id}" in message
        assert "Run fewer memory-heavy commands at once" in message
    finally:
        reset_background_jobs()


def test_foreground_command_is_killed_but_shell_survives(fake_low_memory):
    from gptme.tools.shell import ShellSession

    shell = ShellSession()
    try:
        result: dict = {}
        runner = threading.Thread(
            target=lambda: result.update(value=shell.run("sleep 30", output=False))
        )
        start = time.monotonic()
        runner.start()
        time.sleep(0.5)
        assert fake_low_memory.check() is not None
        runner.join(10)
        assert not runner.is_alive()
        assert time.monotonic() - start < 10
        notice = shell.consume_restart_notice()
        assert notice and "Killed the running shell command" in notice
        assert shell.process.poll() is None  # the shell itself is kept
        assert shell.run("echo still here", output=False)[1].strip() == "still here"
    finally:
        shell.close()


def test_guard_can_be_disabled(monkeypatch):
    monkeypatch.setattr(shell_memory, "_guard", None)
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    shell_memory.watch(Target(1, "x", lambda: None, lambda: True, lambda r: None))
    assert shell_memory._guard is None


def test_background_job_workers_that_ignore_sigterm_are_killed(fake_low_memory):
    """A worker ignoring SIGTERM must not survive its leader exiting on it."""
    from gptme.tools.shell_background import reset_background_jobs, start_background_job
    from gptme.tools.shell_memory import group_alive

    job = start_background_job("(trap '' TERM; exec sleep 300) & wait")
    try:
        time.sleep(0.5)
        pgid = job.process_group_id
        assert pgid is not None and group_alive(pgid)
        assert fake_low_memory.check() is not None
        deadline = time.monotonic() + 5
        while group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not group_alive(pgid)
    finally:
        reset_background_jobs()


def test_guard_is_created_once_under_concurrency(monkeypatch):
    monkeypatch.setattr(shell_memory, "_guard", None)
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "1")
    created: list[MemoryGuard] = []
    real_init = MemoryGuard.__init__

    def slow_init(self, *a, **kw):
        time.sleep(0.05)  # widen the race window
        real_init(self, *a, **kw)
        created.append(self)

    monkeypatch.setattr(MemoryGuard, "__init__", slow_init)
    monkeypatch.setattr(MemoryGuard, "register", lambda self, t: None)
    threads = [
        threading.Thread(
            target=shell_memory.watch,
            args=(Target(i, "x", lambda: None, lambda: True, lambda r: None),),
        )
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(created) == 1


def test_guard_thread_stops_when_nothing_is_watched(monkeypatch):
    monkeypatch.setattr(shell_memory, "INTERVAL", 0.01)
    alive = {"value": True}
    guard = MemoryGuard(memory=lambda: (16 * GB, 0.50, False), rss=dict)
    guard.register(Target(1, "x", lambda: None, lambda: alive["value"], print))
    thread = guard._thread
    assert thread is not None and thread.is_alive()
    alive["value"] = False
    thread.join(2)
    assert not thread.is_alive() and guard._thread is None
    alive["value"] = True
    guard.register(Target(2, "y", lambda: None, lambda: alive["value"], print))
    assert guard._thread is not None and guard._thread is not thread
    alive["value"] = False
    guard._thread.join(2)


def test_worker_killed_after_completion_gets_its_own_notice(fake_low_memory):
    """A worker that outlives its job's main process is still reported."""
    from gptme.tools.shell_background import (
        _completion_queue,
        reset_background_jobs,
        start_background_job,
    )
    from gptme.tools.shell_background import (
        background_job_completion_hook as hook,
    )

    # the worker doesn't hold the job's output pipes, so the job completes
    # as soon as its main process exits
    job = start_background_job("(trap '' HUP; exec sleep 300) >/dev/null 2>&1 & exit 0")

    class Manager:
        chat_id = job.conversation_id

    try:
        deadline = time.monotonic() + 15
        while not job._completion_notified and time.monotonic() < deadline:
            time.sleep(0.1)
        assert job._completion_notified
        [completion] = list(hook(Manager()))
        assert "finished" in completion.content
        assert fake_low_memory.check() is not None
        [late] = list(hook(Manager()))
        assert f"left over from background shell job #{job.id}" in late.content
        assert "system memory was nearly exhausted" in late.content
        assert _completion_queue.empty()
    finally:
        reset_background_jobs()


def test_command_that_execs_is_killed_and_shell_restarts(fake_low_memory):
    """`exec` replaces the shell with the command: the guard must stop it."""
    from gptme.tools.shell import ShellSession

    worker = (
        f"{sys.executable} -c 'import time; b = bytearray(50 * 2**20); time.sleep(30)'"
    )
    command = f"exec sh -c {shlex.quote(f'while :; do {worker}; done')}"
    shell = ShellSession()
    try:
        result: dict = {}
        runner = threading.Thread(
            # a small parent that outlives its bigger workers (`exec make -j8`)
            target=lambda: result.update(value=shell.run(command, output=False))
        )
        runner.start()
        time.sleep(1.5)  # the worker has allocated
        assert fake_low_memory.check() is not None
        runner.join(10)
        assert not runner.is_alive()
        notice = shell.consume_restart_notice()
        assert notice and "Killed the running shell command" in notice
        assert shell.run("echo still here", output=False)[1].strip() == "still here"
    finally:
        shell.close()
