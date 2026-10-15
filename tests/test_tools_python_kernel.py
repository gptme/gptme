"""
Tests for the Jupyter kernel backend (GPTME_IPYTHON_BACKEND=kernel).

These tests require jupyter_client and ipykernel.  They are skipped if those
packages are not installed.  They start a real kernel process and are
therefore slower than the in-process tests.
"""

import os
from unittest.mock import patch

import pytest

# Skip entire module if jupyter_client / ipykernel are not available
pytest.importorskip(
    "jupyter_client",
    reason="jupyter_client not installed (pip install 'gptme[kernel]')",
)
pytest.importorskip(
    "ipykernel", reason="ipykernel not installed (pip install 'gptme[kernel]')"
)

from gptme.tools.python_kernel import BridgeServer, KernelBackend, reset_backend


@pytest.fixture
def backend():
    """Start a fresh KernelBackend per test and shut it down afterwards."""
    kb = KernelBackend()
    yield kb
    kb.shutdown()


# ---------------------------------------------------------------------------
# Basic execution
# ---------------------------------------------------------------------------


def test_kernel_execute_stdout(backend):
    result = backend.execute('print("hello kernel")')
    assert "hello kernel" in result.stdout
    assert result.error is None


def test_kernel_execute_result(backend):
    result = backend.execute("1 + 1")
    assert result.result_text is not None
    assert "2" in result.result_text
    assert result.error is None


def test_kernel_execute_multiline(backend):
    code = "x = 10\ny = 20\nx + y"
    result = backend.execute(code)
    assert result.result_text is not None
    assert "30" in result.result_text


def test_kernel_execute_stderr(backend):
    result = backend.execute("import sys; sys.stderr.write('err line\\n')")
    assert "err line" in result.stderr


def test_kernel_execute_error(backend):
    result = backend.execute("1 / 0")
    assert result.had_error
    assert result.error is not None
    assert "ZeroDivisionError" in result.error["ename"]


def test_kernel_execute_syntax_error(backend):
    result = backend.execute("def :")
    assert result.had_error


# ---------------------------------------------------------------------------
# State persistence between cells
# ---------------------------------------------------------------------------


def test_kernel_state_persists_between_cells(backend):
    backend.execute("x = 42")
    result = backend.execute("x")
    assert result.result_text is not None
    assert "42" in result.result_text


def test_kernel_function_definition_persists(backend):
    backend.execute("def double(n): return n * 2")
    result = backend.execute("double(7)")
    assert result.result_text is not None
    assert "14" in result.result_text


# ---------------------------------------------------------------------------
# Bridge: registered functions are callable from the kernel
# ---------------------------------------------------------------------------


def test_bridge_function_callable_from_kernel(backend):
    """Pure-Python functions registered on the bridge are callable from the kernel."""
    calls: list[tuple] = []

    def my_helper(a, b):
        calls.append((a, b))
        return a + b

    backend.register_bridge_function(my_helper)

    result = backend.execute("my_helper(3, 4)")
    assert result.error is None, result.error
    assert result.result_text is not None
    assert "7" in result.result_text
    assert calls == [(3, 4)]


def test_bridge_function_error_propagates(backend):
    """A bridge function that raises should produce a RuntimeError in the kernel."""

    def always_fails():
        raise ValueError("intentional failure")

    backend.register_bridge_function(always_fails)

    result = backend.execute("always_fails()")
    assert result.had_error
    # The RuntimeError from the bridge stub should appear
    assert result.error is not None


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------


def test_kernel_interrupt_idle_kernel_is_noop(backend):
    """Calling interrupt() on an idle kernel doesn't break it."""
    backend.interrupt()  # should be a no-op
    result = backend.execute("42")
    assert result.result_text is not None
    assert "42" in result.result_text


def test_kernel_interrupt_via_timeout(backend):
    """A cell that exceeds the timeout gets interrupted and execute() returns."""
    import time

    start = time.monotonic()
    # timeout=2 means the loop sends interrupt and breaks after 2s
    result = backend.execute("import time; time.sleep(100)", timeout=2)
    assert result.error is not None
    assert result.error["ename"] == "ExecutionTimeout"
    elapsed = time.monotonic() - start
    # Should return within ~3 seconds (2s timeout + slack)
    assert elapsed < 6, f"execute() took {elapsed:.1f}s after timeout"

    # Kernel should still be usable
    result2 = backend.execute("1 + 1")
    assert result2.result_text is not None
    assert "2" in result2.result_text


# ---------------------------------------------------------------------------
# Shutdown / restart
# ---------------------------------------------------------------------------


def test_kernel_shutdown_and_restart(backend):
    """After shutdown, a new execute should restart automatically."""
    backend.execute("x = 1")
    backend.shutdown()
    # After shutdown, the backend restarts on next execute
    result = backend.execute("1 + 1")
    assert result.result_text is not None
    assert "2" in result.result_text


# ---------------------------------------------------------------------------
# Integration: execute_python with GPTME_IPYTHON_BACKEND=kernel
# ---------------------------------------------------------------------------


def test_execute_python_kernel_mode():
    """execute_python routes to the kernel backend when env var is set."""
    from gptme.tools.python import execute_python

    reset_backend()  # ensure fresh backend
    try:
        with patch.dict(os.environ, {"GPTME_IPYTHON_BACKEND": "kernel"}):
            # Reload _KERNEL_MODE — need to patch at module level
            import gptme.tools.python as py_mod

            original = py_mod._KERNEL_MODE
            py_mod._KERNEL_MODE = True
            try:
                msgs = list(execute_python('print("from_kernel")', [], None))
            finally:
                py_mod._KERNEL_MODE = original
    finally:
        reset_backend()

    assert msgs
    content = msgs[0].content
    assert "from_kernel" in content
    assert "Kernel error:" not in content


# ---------------------------------------------------------------------------
# BridgeServer unit tests
# ---------------------------------------------------------------------------


def _bridge_call(server: BridgeServer, payload: bytes) -> bytes:
    """Helper: send auth token + framed payload, return framed response bytes."""
    import socket
    import struct

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect(("127.0.0.1", server.port))
        s.sendall(server._token)
        s.sendall(struct.pack(">I", len(payload)) + payload)
        raw_len = s.recv(4)
        (length,) = struct.unpack(">I", raw_len)
        data = b""
        while len(data) < length:
            data += s.recv(length - len(data))
    return data


def test_bridge_server_register_and_call():
    """BridgeServer round-trips a call over the socket."""
    import pickle

    server = BridgeServer()
    server.start()

    def add(a, b):
        return a + b

    server.register(add)

    payload = pickle.dumps({"fn": "add", "args": (5, 6), "kwargs": {}})
    data = _bridge_call(server, payload)
    result = pickle.loads(data)
    server.stop()

    assert "result" in result
    assert result["result"] == 11


def test_bridge_server_unknown_function():
    import pickle

    server = BridgeServer()
    server.start()

    payload = pickle.dumps({"fn": "nonexistent", "args": (), "kwargs": {}})
    data = _bridge_call(server, payload)
    result = pickle.loads(data)
    server.stop()

    assert "error" in result


def test_bridge_server_rejects_wrong_token():
    """Bridge closes connection silently on wrong auth token."""
    import pickle
    import socket
    import struct

    server = BridgeServer()
    server.start()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", server.port))
            # Send wrong token (all zeros)
            s.sendall(bytes(len(server._token)))
            payload = pickle.dumps({"fn": "add", "args": (1, 2), "kwargs": {}})
            s.sendall(struct.pack(">I", len(payload)) + payload)
            s.settimeout(2.0)
            # Server closes without sending a response — either EOF or RST is fine
            try:
                data = s.recv(4)
                assert data == b"", "Expected server to close connection on wrong token"
            except ConnectionResetError:
                pass  # RST also means the server rejected us
    finally:
        server.stop()


def test_kernel_capture_caps_single_large_chunk(backend, monkeypatch):
    import gptme.tools.python_kernel as kernel

    monkeypatch.setattr(kernel, "_MAX_STREAMING_OUTPUT_BYTES", 64)
    result = backend.execute("print('x' * 10000)")
    assert len(result.stdout) < 256
    assert "bytes omitted" in result.stdout


def test_kernel_caps_error_text(backend, monkeypatch):
    import gptme.tools.python_kernel as kernel

    monkeypatch.setattr(kernel, "_MAX_STREAMING_OUTPUT_BYTES", 64)
    result = backend.execute("raise ValueError('x' * 10000)")
    assert result.error is not None
    assert len(result.error["evalue"]) < 256
    assert len("\n".join(result.error["traceback"])) < 256


def test_kernel_bridge_preserves_message_files(backend, tmp_path, monkeypatch):
    import gptme.tools.python as py_mod
    from gptme.message import Message

    image = tmp_path / "plot.png"
    image.write_bytes(b"image")
    message = Message("system", "image result", files=[image])

    def image_helper():
        return [message]

    backend.register_bridge_function(image_helper)
    monkeypatch.setattr("gptme.tools.python_kernel.get_backend", lambda: backend)
    messages = list(py_mod._execute_python_kernel("image_helper()"))
    assert any(msg.files == [image] for msg in messages)


def test_kernel_syncs_cwd_after_python_chdir(backend, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    backend.execute(f"import os; os.chdir({str(other)!r})")
    result = backend.execute("os.getcwd()")
    assert result.result_text == repr(str(tmp_path))


def test_bridge_helper_uses_kernel_cwd(backend, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()

    def helper_cwd():
        return os.getcwd()

    backend.register_bridge_function(helper_cwd)
    result = backend.execute(f"import os; os.chdir({str(other)!r}); helper_cwd()")
    assert result.error is not None
    assert "working directory" in result.error["evalue"]
    assert os.getcwd() == str(tmp_path)


def test_bridge_stop_closes_idle_connections():
    import socket
    import time

    server = BridgeServer()
    server.start()
    with socket.create_connection(("127.0.0.1", server.port), timeout=2) as conn:
        deadline = time.monotonic() + 2
        while not server._connections and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server._connections
        server.stop()
        assert conn.recv(1) == b""


def test_kernel_bridge_preserves_single_message(backend):
    from gptme.message import Message

    message = Message("system", "helper result")

    def message_helper():
        return message

    backend.register_bridge_function(message_helper)
    result = backend.execute("message_helper()")
    assert result.error is None
    assert backend._bridge.messages == [message]
    backend.execute("42")
    assert not backend._bridge.messages


def test_kernel_helpers_survive_restart(backend):
    def helper():
        return 73

    backend.register_bridge_function(helper)
    assert backend.execute("helper()").result_text == "73"
    backend.shutdown()
    assert backend.execute("helper()").result_text == "73"


def test_kernel_message_return_remains_usable(backend):
    from gptme.message import Message

    def message_value():
        return Message("system", "usable result")

    backend.register_bridge_function(message_value)
    result = backend.execute("message_value().content")
    assert result.error is None
    assert result.result_text == "'usable result'"
    assert backend._bridge.messages[0].content == "usable result"


def test_bridge_different_cwd_does_not_change_host(backend, tmp_path, monkeypatch):
    import threading

    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    entered = threading.Event()
    release = threading.Event()
    observed = []

    def slow_helper():
        entered.set()
        release.wait(5)
        return "done"

    backend.register_bridge_function(slow_helper)

    def observe():
        entered.wait(2)
        observed.append(os.getcwd())
        release.set()

    observer = threading.Thread(target=observe)
    observer.start()
    try:
        result = backend.execute(f"import os; os.chdir({str(other)!r}); slow_helper()")
    finally:
        release.set()
        observer.join(3)
    assert os.getcwd() == str(tmp_path)
    assert observed == [str(tmp_path)]
    assert result.error is not None
    assert "working directory" in result.error["evalue"]


def test_cancelled_helper_message_does_not_reach_next_cell(backend):
    import threading

    from gptme.message import Message

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def late_message():
        entered.set()
        release.wait(10)
        finished.set()
        return Message("system", "stale result")

    backend.register_bridge_function(late_message)
    try:
        result = backend.execute("late_message()", timeout=0.5)
        assert entered.is_set()
        assert result.error is not None
        # Finish the canceled helper while the next cell is already running,
        # after its message buffer has been cleared.
        timer = threading.Timer(0.2, release.set)
        timer.start()
        try:
            result = backend.execute("import time; time.sleep(0.5); 42")
        finally:
            timer.join(2)
        assert finished.wait(2)
        assert result.error is None
        assert not backend._bridge.messages
    finally:
        release.set()


def test_bridge_preserves_each_cell_session_context(backend):
    from contextvars import copy_context

    from gptme.tools._url_safety import (
        _validate_url_scheme,
        set_session_allow_hosts,
    )

    def check_url(url):
        _validate_url_scheme(url)
        return "allowed"

    backend.register_bridge_function(check_url)

    def execute_restricted(host):
        set_session_allow_hosts([host])
        return backend.execute("check_url('https://blocked.example/')")

    result = copy_context().run(execute_restricted, "allowed.example")
    assert result.error is not None
    assert "allowed-hosts" in result.error["evalue"]
    # Reusing the backend must capture the next cell's context, not startup's.
    result = copy_context().run(execute_restricted, "blocked.example")
    assert result.error is None
    assert result.result_text == "'allowed'"


def test_bridge_rejects_oversized_frame_before_reading_payload():
    import socket
    import struct

    sender, receiver = socket.socketpair()
    with sender, receiver:
        sender.sendall(struct.pack(">I", 0xFFFFFFFF))
        sender.shutdown(socket.SHUT_WR)
        with pytest.raises(ValueError, match="frame too large"):
            BridgeServer._recv_framed(receiver)


def test_silent_setup_timeout_stops_kernel(backend):
    from unittest.mock import Mock

    backend._kc = Mock()
    backend._km = Mock()
    with pytest.raises(RuntimeError, match="setup timed out"):
        backend._exec_silent("pass", timeout=0)
    backend._km.interrupt_kernel.assert_called_once()


def test_silent_setup_error_is_not_ignored(backend):
    from unittest.mock import Mock

    backend._kc = Mock()
    backend._kc.execute.return_value = "setup"
    backend._kc.get_shell_msg.return_value = {
        "parent_header": {"msg_id": "setup"},
        "content": {"status": "error", "ename": "OSError", "evalue": "bad cwd"},
    }
    with pytest.raises(RuntimeError, match="bad cwd"):
        backend._exec_silent("pass")


def test_kernel_execute_display_output(backend):
    result = backend.execute(
        "from IPython.display import display; display('hello display')"
    )
    assert "hello display" in result.stdout
    assert result.error is None


# ---------------------------------------------------------------------------
# Memory guard + kernel death (gptme/gptme#4355: "the memory guard watches the
# kernel's process group")
# ---------------------------------------------------------------------------


def test_kernel_registers_its_process_group_with_memory_guard(backend):
    with patch("gptme.tools.python_kernel.watch_memory") as watch:
        backend.execute("1")
    assert watch.call_count == 1
    target = watch.call_args.args[0]
    kernel_pid = backend._km.provisioner.pid
    assert target.pgid == os.getpgid(kernel_pid)
    # The kernel runs in its own session: never register gptme's own group.
    assert target.pgid != os.getpgid(0)
    assert "kernel" in target.label
    assert target.alive() is True
    backend.shutdown()
    assert target.alive() is False


def test_watch_memory_is_noop_on_non_posix(backend, monkeypatch):
    """The guard must not be registered on Windows (os.getpgid is Unix-only)."""
    backend.execute("1")  # start the kernel for real
    monkeypatch.setattr(os, "name", "nt")
    with patch("gptme.tools.python_kernel.watch_memory") as watch:
        backend._watch_memory()
    assert watch.call_count == 0


def test_kernel_death_mid_cell_is_reported_promptly(backend):
    """A kernel that dies while a cell runs must not block until the timeout."""
    import time

    start = time.monotonic()
    result = backend.execute(
        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)", timeout=30
    )
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"blocked {elapsed:.1f}s waiting on a dead kernel"
    assert result.error is not None
    assert result.error["ename"] == "KernelDied"
    # The next cell restarts the kernel transparently.
    assert backend.execute("1 + 1").result_text == "2"


def test_old_kernel_workers_do_not_report_current_state_lost(backend):
    """A retained guard callback must describe its own kernel generation."""
    with patch("gptme.tools.python_kernel.watch_memory") as watch:
        backend.execute("old_state = 1")
        old_target = watch.call_args.args[0]
        backend.shutdown()
        backend.execute("current_state = 42")
        current_target = watch.call_args.args[0]
    assert old_target.pgid != current_target.pgid

    # Simulate the guard cleaning up workers left by the earlier kernel.
    old_target.on_killed(
        "Killed the IPython kernel: system memory was nearly exhausted"
    )
    result = backend.execute("current_state")
    assert result.result_text == "42"
    assert "earlier cells are lost" not in result.stderr
    assert "current kernel state is unchanged" in result.stderr
    assert "system memory was nearly exhausted" in result.stderr
    assert "system memory was nearly exhausted" not in backend.execute("1").stderr

    # The replacement's own callback must still report genuine state loss.
    current_target.on_killed(
        "Killed the IPython kernel: system memory was nearly exhausted"
    )
    current_target.kill()
    result = backend.execute("1 + 1")
    assert result.result_text == "2"
    assert "earlier cells are lost" in result.stderr


def test_memory_guard_kill_reason_reaches_the_model(backend):
    """When the guard kills the kernel, its explanation surfaces in the output."""
    with patch("gptme.tools.python_kernel.watch_memory") as watch:
        backend.execute("x = 1")
    target = watch.call_args.args[0]
    # Same order as MemoryGuard.check(): explain, then kill.
    target.on_killed("Killed the IPython kernel: system memory was nearly exhausted")
    target.kill()
    result = backend.execute("1 + 1")
    assert result.result_text == "2"
    assert "Killed the IPython kernel" in result.stderr
    assert "earlier cells are lost" in result.stderr
    # Reported once, not on every later cell.
    assert "Killed the IPython kernel" not in backend.execute("2 + 2").stderr


# ---------------------------------------------------------------------------
# uv run --with ipykernel support
# ---------------------------------------------------------------------------


def test_find_uv_returns_none_when_not_available(monkeypatch):
    """_find_uv returns None when uv is not on PATH."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert KernelBackend._find_uv() is None


def test_find_uv_returns_path_when_available(monkeypatch):
    """_find_uv returns a path string when uv is on PATH."""
    import shutil

    monkeypatch.setattr(
        shutil, "which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None
    )
    result = KernelBackend._find_uv()
    assert result == "/usr/bin/uv"


def test_is_uv_project_true_when_uv_lock_exists(tmp_path, monkeypatch):
    """_is_uv_project returns True when uv.lock is present."""
    (tmp_path / "uv.lock").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))
    assert KernelBackend._is_uv_project() is True


def test_is_uv_project_false_when_no_lock(tmp_path, monkeypatch):
    """_is_uv_project returns False when no uv.lock exists and no pyproject.toml."""
    monkeypatch.chdir(tmp_path)
    assert KernelBackend._is_uv_project() is False


def test_is_uv_project_true_with_pyproject_no_poetry(tmp_path, monkeypatch):
    """_is_uv_project returns True for pyproject.toml without poetry.lock when uv available."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))
    assert KernelBackend._is_uv_project() is True


def test_is_uv_project_false_when_poetry_lock(tmp_path, monkeypatch):
    """_is_uv_project returns False when poetry.lock exists (poetry project)."""
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'\n")
    (tmp_path / "poetry.lock").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))
    assert KernelBackend._is_uv_project() is False


def test_build_uv_kernel_cmd_returns_none_without_uv(monkeypatch):
    """_build_uv_kernel_cmd returns None when uv is not available."""
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: None))
    assert KernelBackend._build_uv_kernel_cmd() is None


def test_build_uv_kernel_cmd_includes_with_ipykernel(monkeypatch):
    """_build_uv_kernel_cmd returns a command that injects ipykernel via uv."""
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))
    cmd = KernelBackend._build_uv_kernel_cmd()
    assert cmd is not None
    assert "--with" in cmd
    assert "ipykernel" in cmd
    assert "ipykernel_launcher" in cmd
    assert "{connection_file}" in cmd


class _FakeKC:
    """Minimal stand-in for ``BlockingKernelClient`` — no real channels."""

    def start_channels(self):
        pass

    def wait_for_ready(self, timeout=None):
        pass

    def stop_channels(self):
        pass


def _run_ensure_started_and_capture_cmd(monkeypatch) -> list[str]:
    """Run ``_ensure_started`` with a real KernelManager and return the launch cmd.

    Uses a real ``jupyter_client.KernelManager`` subclass so the assertion checks
    the command jupyter_client would actually launch (built from
    ``kernel_spec.argv`` via ``format_kernel_cmd``), not an attribute it ignores.
    """
    import jupyter_client
    from jupyter_client import KernelManager as RealKernelManager

    captured: list[list[str]] = []

    class RecordingKM(RealKernelManager):
        def start_kernel(self, **_kw):
            # Record exactly what the real launch path builds, then skip the
            # actual subprocess launch.
            captured.append(self.format_kernel_cmd(extra_arguments=[]))

        def client(self):
            return _FakeKC()

        def is_alive(self):
            return True

        def shutdown_kernel(self, **_kw):
            pass

    monkeypatch.setattr(jupyter_client, "KernelManager", RecordingKM)

    kb = KernelBackend()
    # Stub out _exec_silent so the bootstrap step doesn't run.
    monkeypatch.setattr(kb, "_exec_silent", lambda *_a, **_kw: None)

    kb._ensure_started()
    kb._bridge.stop()

    assert len(captured) == 1
    return captured[0]


def test_ensure_started_uses_uv_when_project_python_lacks_ipykernel(
    monkeypatch, tmp_path
):
    """When project Python lacks ipykernel, the launched kernel uses uv run --with ipykernel."""
    # Simulate a project venv whose Python lacks ipykernel.
    fake_python = str(tmp_path / "python")
    monkeypatch.setattr(
        KernelBackend, "_find_project_python", staticmethod(lambda: fake_python)
    )
    monkeypatch.setattr(KernelBackend, "_has_ipykernel", staticmethod(lambda _: False))
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))

    cmd = _run_ensure_started_and_capture_cmd(monkeypatch)

    assert cmd[:2] == ["/usr/bin/uv", "run"]
    assert "--with" in cmd
    assert "ipykernel" in cmd
    assert "ipykernel_launcher" in cmd


def test_ensure_started_uv_when_uv_project_without_venv(monkeypatch, tmp_path):
    """A uv project with no activated venv launches via uv run --with ipykernel."""
    (tmp_path / "uv.lock").write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        KernelBackend, "_find_project_python", staticmethod(lambda: None)
    )
    monkeypatch.setattr(KernelBackend, "_find_uv", staticmethod(lambda: "/usr/bin/uv"))

    cmd = _run_ensure_started_and_capture_cmd(monkeypatch)

    assert cmd[:2] == ["/usr/bin/uv", "run"]
    assert "--with" in cmd and "ipykernel" in cmd


def test_ensure_started_uses_project_python_when_ipykernel_present(
    monkeypatch, tmp_path
):
    """Project Python with ipykernel is used directly as the kernel command."""
    project_python = str(tmp_path / "python")
    monkeypatch.setattr(
        KernelBackend, "_find_project_python", staticmethod(lambda: project_python)
    )
    monkeypatch.setattr(KernelBackend, "_has_ipykernel", staticmethod(lambda _: True))

    cmd = _run_ensure_started_and_capture_cmd(monkeypatch)

    assert cmd[0] == project_python
    assert "ipykernel_launcher" in cmd
