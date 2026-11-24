"""Ctrl-C while a script hook runs leaves the hook's process tree running.

_run_script_hook starts the command in its own session (start_new_session=True),
so terminal SIGINT never reaches it. KeyboardInterrupt raised in process.wait()
is not an ``Exception`` and bypasses every cleanup branch, so the child is
orphaned and keeps running after gptme gave up on it.
"""

import subprocess
import sys
import time

import pytest

from gptme.config import ScriptHookConfig
from gptme.hooks.script import _run_script_hook


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX")
def test_keyboard_interrupt_kills_script_hook_process(tmp_path, monkeypatch):
    spawned: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    class RecordingPopen(real_popen):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            spawned.append(self)
            self._interrupted = False

        def wait(self, timeout=None):
            # Simulate the user pressing Ctrl-C while gptme waits on the hook.
            if not self._interrupted and timeout == 30:
                self._interrupted = True
                time.sleep(0.2)
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr("gptme.hooks.script.subprocess.Popen", RecordingPopen)

    hook = ScriptHookConfig(event="session.start", command="sleep 30", timeout=30)
    try:
        with pytest.raises(KeyboardInterrupt):
            _run_script_hook(hook, tmp_path, logdir=tmp_path, model="m")

        assert len(spawned) == 1
        proc = spawned[0]
        # No poll()/sleep first: poll() would reap the shell itself and hide a
        # runner that killed it without waiting.
        assert proc.returncode is not None, (
            "script hook process was not reaped before KeyboardInterrupt propagated"
        )
    finally:
        for p in spawned:
            if p.poll() is None:
                import os
                import signal

                os.killpg(p.pid, signal.SIGKILL)
                p.wait()


def test_keyboard_interrupt_logs_when_reap_times_out(tmp_path, monkeypatch, caplog):
    """If the process won't exit after the interrupt, log and still re-raise."""
    from gptme.hooks import script

    class StubbornProcess:
        pid = 4242

        def wait(self, timeout=None):
            if timeout == 30:
                raise KeyboardInterrupt
            raise subprocess.TimeoutExpired("sleep 30", timeout)

    terminated: list[StubbornProcess] = []
    monkeypatch.setattr(script.subprocess, "Popen", lambda *a, **kw: StubbornProcess())
    monkeypatch.setattr(script, "_terminate_process_tree", terminated.append)

    hook = ScriptHookConfig(event="session.start", command="sleep 30", timeout=30)
    with (
        caplog.at_level("ERROR", logger=script.logger.name),
        pytest.raises(KeyboardInterrupt),
    ):
        _run_script_hook(hook, tmp_path, logdir=tmp_path, model="m")

    assert len(terminated) == 1
    assert "did not exit after interrupt" in caplog.text


def test_keyboard_interrupt_kills_shell_when_tree_kill_fails(tmp_path, monkeypatch):
    """A failing process-tree kill must not replace the interrupt or skip the kill."""
    from gptme.hooks import script

    class Process:
        pid = 4243
        killed = False
        reaped = False

        def wait(self, timeout=None):
            if timeout == 30:
                raise KeyboardInterrupt
            self.reaped = True
            return -9

        def kill(self):
            self.killed = True

    proc = Process()

    def tree_kill_fails(process):
        raise RuntimeError("taskkill failed")

    monkeypatch.setattr(script.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(script, "_terminate_process_tree", tree_kill_fails)

    hook = ScriptHookConfig(event="session.start", command="sleep 30", timeout=30)
    with pytest.raises(KeyboardInterrupt):
        _run_script_hook(hook, tmp_path, logdir=tmp_path, model="m")

    assert proc.killed
    assert proc.reaped
