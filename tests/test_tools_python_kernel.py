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
    backend.execute("import time; time.sleep(100)", timeout=2)
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
