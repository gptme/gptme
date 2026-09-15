"""CLI termination must run background-job cleanup without making model calls."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="POSIX signals and process groups"
)


@pytest.mark.parametrize("mode", ["terminate", "default-return", "ignore", "custom"])
def test_cli_sigterm_cleans_background_job(tmp_path: Path, mode: str) -> None:
    ready = tmp_path / "ready"
    script = r"""
import importlib
import signal
import socket
import sys
from pathlib import Path

# Fail closed on accidental network access; all configuration lives in tmp_path.
def no_network(*args, **kwargs):
    raise AssertionError("network access in SIGTERM regression")
socket.socket.connect = no_network

from gptme.tools.shell_background import start_background_job
import gptme.tools
import gptme.prompts
import gptme.telemetry

gptme.tools.init_tools = lambda *args, **kwargs: []
gptme.prompts.get_prompt = lambda *args, **kwargs: []
gptme.telemetry.init_telemetry = lambda *args, **kwargs: None

ready = Path(sys.argv[1])
mode = sys.argv[2]
if mode == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
elif mode == "custom":
    signal.signal(signal.SIGTERM, lambda signum, frame: None)
previous_handler = signal.getsignal(signal.SIGTERM)
def fake_chat(*args, **kwargs):
    job = start_background_job("exec sleep 120")
    temporary = ready.with_suffix(".tmp")
    temporary.write_text(str(job.process.pid))
    temporary.replace(ready)
    if mode == "terminate":
        signal.pause()
    elif mode == "default-return":
        assert callable(signal.getsignal(signal.SIGTERM))
    else:
        assert signal.getsignal(signal.SIGTERM) == previous_handler

importlib.import_module("gptme.chat").chat = fake_chat
from gptme.cli.main import main
main(
    ["--non-interactive", "--no-workspace", "--model", "openai/gpt-4o", "hello"],
    standalone_mode=False,
)
assert signal.getsignal(signal.SIGTERM) == previous_handler
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GPTME_", "OPENAI_", "ANTHROPIC_", "OPENROUTER_"))
    }
    env.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1]),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    child_pid = None
    with subprocess.Popen(
        [sys.executable, "-c", script, str(ready), mode],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and process.poll() is None:
                assert time.monotonic() < deadline, "CLI did not start background job"
                time.sleep(0.02)
            if not ready.exists():
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail(f"CLI failed before job start: {stdout}\n{stderr}")
            child_pid = int(ready.read_text())
            if mode == "terminate":
                os.kill(child_pid, 0)
                process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
            expected_code = 128 + signal.SIGTERM if mode == "terminate" else 0
            assert process.returncode == expected_code, (stdout, stderr)
        finally:
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
