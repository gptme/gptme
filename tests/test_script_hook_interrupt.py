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
        time.sleep(0.3)
        assert proc.poll() is not None, (
            "script hook process still running after KeyboardInterrupt"
        )
    finally:
        for p in spawned:
            if p.poll() is None:
                import os
                import signal

                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
