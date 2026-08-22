"""Integration test for server startup — verifies the server actually binds.

Regression test for gptme/gptme#3589: "SERVER: Silent startup failure —
process exits without binding port". The `serve` command was marked
`# pragma: no cover`, so any startup regression (silent sys.exit, unhandled
exception swallowed by click, port-bind failure) would go undetected.

This test spawns the real server as a subprocess and polls the port directly.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip(
    "flask", reason="flask not installed, install server extras (-E server)"
)

_STARTUP_TIMEOUT = 15.0
_POLL_INTERVAL = 0.1


def _find_free_port() -> int:
    """Return an OS-assigned free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(_POLL_INTERVAL)
    return False


@pytest.fixture
def server_process(tmp_path):
    host = "127.0.0.1"
    port = _find_free_port()
    env = os.environ.copy()
    env["GPTME_DISABLE_AUTH"] = "1"
    env["HOME"] = str(tmp_path)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "gptme.server.cli", "--host", host, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    yield proc, host, port
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_server_binds_to_port(server_process):
    proc, host, port = server_process
    bound = _wait_for_port(host, port, _STARTUP_TIMEOUT)
    if not bound:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        exit_code = proc.returncode
        raise AssertionError(
            f"Server did not bind to {host}:{port} within {_STARTUP_TIMEOUT}s.\n"
            f"Exit code: {exit_code}\n"
            f"stdout:\n{stdout.decode(errors='replace')}\n"
            f"stderr:\n{stderr.decode(errors='replace')}"
        )


def test_server_responds_to_api_root(server_process):
    import urllib.error
    import urllib.request

    proc, host, port = server_process
    if not _wait_for_port(host, port, _STARTUP_TIMEOUT):
        pytest.fail(f"Server did not start within {_STARTUP_TIMEOUT}s")
    url = f"http://{host}:{port}/api/v2"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
    except urllib.error.URLError as exc:
        pytest.fail(f"HTTP request to {url} failed: {exc}")


def test_server_exits_nonzero_on_bad_webui_dir(tmp_path):
    env = os.environ.copy()
    env["GPTME_DISABLE_AUTH"] = "1"
    env["HOME"] = str(tmp_path)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    bad_dir = str(tmp_path / "does-not-exist")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gptme.server.cli",
            "--host",
            "127.0.0.1",
            "--port",
            str(_find_free_port()),
            "--webui-dir",
            bad_dir,
        ],
        capture_output=True,
        timeout=10,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).decode(errors="replace")
    assert combined.strip(), (
        "Server exited non-zero but produced NO output — silent failure!"
    )
