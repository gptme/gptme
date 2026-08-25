"""Integration test for server startup — verifies the server actually binds.

Regression test for gptme/gptme#3589: "SERVER: Silent startup failure —
process exits without binding port". The `serve` command was marked
`# pragma: no cover`, so any startup regression (silent sys.exit, unhandled
exception swallowed by click, port-bind failure) would go undetected.

#3589 root cause: ``_install_sigterm_handler()`` was called just before
``app.run()``, AFTER the slow initialisation phase (model init, telemetry).
If SIGTERM arrived during that phase (e.g. from ``timeout 10 uv run ...``),
Python's default SIGTERM handler terminated the process immediately with no
diagnostic output — the user saw config logs then silence.

Fix: install the SIGTERM handler right after ``init_logging()``, before the
slow init work, so any SIGTERM during startup is caught and logged.

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


def test_sigterm_produces_diagnostic_output(tmp_path):
    """SIGTERM must produce diagnostic output — not silently terminate.

    Regression for gptme/gptme#3589: before the fix, SIGTERM arriving while
    model/telemetry init was running used Python's default handler (immediate
    silent termination). After the fix, the handler is installed at cli.py
    module level (before any heavy imports) and raises KeyboardInterrupt, which
    Click routes to an Aborted message.

    We wait for the server to bind before sending SIGTERM so the test is
    deterministic on both fast and slow CI runners.  The early-install property
    (handler active before any heavy imports) is verified by the companion test
    test_startup_sigterm_handler_installed_at_import.
    """
    env = os.environ.copy()
    env["GPTME_DISABLE_AUTH"] = "1"
    env["HOME"] = str(tmp_path)
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gptme.server.cli",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    # Wait for the server to bind before sending SIGTERM.  A fixed-delay
    # approach is flaky: on fast runners the server starts before the delay
    # expires (so _handle_sigterm runs), on slow/busy runners the delay may
    # expire before Python even installs the signal handler (so the default
    # OS handler silently terminates the process).  Port polling eliminates
    # the race: by the time _wait_for_port returns, both signal handlers
    # (_startup_sigterm_handler → _install_sigterm_handler upgrade) have run.
    if not _wait_for_port("127.0.0.1", port, _STARTUP_TIMEOUT):
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        pytest.fail(
            f"Server did not bind to 127.0.0.1:{port} within {_STARTUP_TIMEOUT}s — "
            "cannot test SIGTERM handling.\n"
            f"stdout:\n{stdout.decode(errors='replace')}\n"
            f"stderr:\n{stderr.decode(errors='replace')}"
        )
    proc.send_signal(__import__("signal").SIGTERM)
    try:
        stdout, stderr = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    combined = (stdout + stderr).decode(errors="replace")
    assert "SIGTERM" in combined or "gracefully" in combined or "Aborted" in combined, (
        "Server received SIGTERM but produced NO diagnostic output — "
        "silent shutdown regression (gptme/gptme#3589).\n"
        f"stdout:\n{stdout.decode(errors='replace')}\n"
        f"stderr:\n{stderr.decode(errors='replace')}"
    )


def test_startup_sigterm_handler_installed_at_import():
    """The module-level SIGTERM handler is active the moment cli.py is imported.

    Regression guard for gptme/gptme#3589: the fix installs the handler at
    module level (before slow imports).  This test verifies that property
    directly — in isolation from serve() — so the test would FAIL if the
    handler were moved back inside serve() or _install_sigterm_handler().
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys;"
                "import gptme.server.cli as cli;"
                "h = signal.getsignal(signal.SIGTERM);"
                "name = getattr(h, '__name__', '');"
                "sys.exit(0 if 'startup' in name else 1)"
            ),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        "Expected _startup_sigterm_handler to be installed at cli.py import time, "
        "but a different handler was active — SIGTERM-during-init regression risk.\n"
        f"stdout: {result.stdout.decode(errors='replace')}\n"
        f"stderr: {result.stderr.decode(errors='replace')}"
    )


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


def test_import_does_not_override_custom_callable_handler():
    """Importing gptme.server.cli must leave a pre-installed callable handler intact.

    An embedder may install its own graceful-shutdown handler before importing
    the CLI module.  That callable handler must not be overridden.

    Regression guard for the gptme/gptme#3597 P2 (module-level guard must not
    unconditionally override every existing disposition).  Note: SIG_IGN is
    intentionally treated like SIG_DFL (the startup handler is installed over
    it) because SIG_IGN can be *inherited* from a parent process such as a
    test runner or daemon supervisor — it does not reliably indicate a
    deliberate embedder choice, and refusing to install over it broke CI.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import signal, sys;"
                # Simulate an embedder that installs a real callable handler.
                "embedder_handler = lambda s, f: None;"
                "signal.signal(signal.SIGTERM, embedder_handler);"
                "import gptme.server.cli as cli;"
                "h = signal.getsignal(signal.SIGTERM);"
                # The exact embedder callable must still be installed — not
                # merely a callable whose name lacks 'startup' (that would
                # pass vacuously for any replacement handler).
                "sys.exit(0 if h is embedder_handler else 1)"
            ),
        ],
        capture_output=True,
        timeout=30,
        check=False,
        cwd=repo_root,
        env=env,
    )
    assert result.returncode == 0, (
        "Expected a pre-installed callable SIGTERM handler to survive "
        "gptme.server.cli import, but the module-level install overrode it "
        "(gptme/gptme#3597 P2).\n"
        f"stdout: {result.stdout.decode(errors='replace')}\n"
        f"stderr: {result.stderr.decode(errors='replace')}"
    )
