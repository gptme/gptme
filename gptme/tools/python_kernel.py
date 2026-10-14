"""
Jupyter kernel backend for the IPython tool.

Runs IPython cells in a separate kernel process, enabling:
- Reliable interruption via SIGINT (even for C-extension blocking calls)
- Project environment isolation (%pip install goes to project env)
- Process isolation (kernel crash doesn't take down gptme)

Enable with: GPTME_IPYTHON_BACKEND=kernel

The kernel is started lazily on first use and restarted on crash.
A socket-based bridge lets kernel-side code call registered gptme
functions (pure-data helpers); Message-returning tool functions
are not yet bridged and remain in-process.
"""

import logging
import os
import pickle
import secrets
import socket
import struct
import subprocess
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_MAX_BRIDGE_CONNECTIONS = 16
_BRIDGE_HANDLER_TIMEOUT = (
    30.0  # seconds — prevents idle connections from leaking threads
)
_MAX_STREAMING_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 MB, matches python.py cap


# ---------------------------------------------------------------------------
# Bridge server: lets kernel stubs call back into gptme's process
# ---------------------------------------------------------------------------


class BridgeServer:
    """TCP server that dispatches RPC calls from kernel stubs."""

    def __init__(self) -> None:
        self._functions: dict[str, Callable] = {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0
        # Per-instance auth token sent by the kernel before every request.
        # Prevents other local processes from sending crafted pickles to the bridge.
        self._token: bytes = secrets.token_bytes(16)
        # Bound semaphore limits concurrent handler threads, preventing fd exhaustion.
        self._semaphore = threading.BoundedSemaphore(_MAX_BRIDGE_CONNECTIONS)

    def register(self, fn: Callable) -> None:
        self._functions[fn.__name__] = fn

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name="gptme-bridge"
        )
        self._thread.start()
        logger.debug("Bridge server listening on port %d", self.port)

    def _serve(self) -> None:
        while True:
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            if not self._semaphore.acquire(blocking=False):
                # Connection limit reached — reject immediately
                try:
                    conn.close()
                except OSError:
                    pass
                logger.warning("Bridge connection limit reached, rejecting connection")
                continue
            t = threading.Thread(
                target=self._handle_with_release, args=(conn,), daemon=True
            )
            t.start()

    def _handle_with_release(self, conn: socket.socket) -> None:
        try:
            self._handle(conn)
        finally:
            self._semaphore.release()

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> bytes:
        """Read exactly *n* bytes from *conn*, raising EOFError on early close."""
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _recv_framed(conn: socket.socket) -> bytes:
        raw = BridgeServer._recv_exact(conn, 4)
        (length,) = struct.unpack(">I", raw)
        buf = bytearray()
        while len(buf) < length:
            chunk = conn.recv(length - len(buf))
            if not chunk:
                raise EOFError
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _send_framed(conn: socket.socket, data: bytes) -> None:
        conn.sendall(struct.pack(">I", len(data)) + data)

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(_BRIDGE_HANDLER_TIMEOUT)
        try:
            # Verify the auth token before unpickling anything.
            token = self._recv_exact(conn, len(self._token))
            if token != self._token:
                logger.warning("Bridge: rejected connection with wrong auth token")
                return

            payload = self._recv_framed(conn)
            call: dict = pickle.loads(payload)
            fn_name: str = call["fn"]
            args: tuple = call.get("args", ())
            kwargs: dict = call.get("kwargs", {})

            fn = self._functions.get(fn_name)
            if fn is None:
                result: dict = {
                    "error": f"Function {fn_name!r} not registered in bridge"
                }
            else:
                try:
                    ret = fn(*args, **kwargs)
                    result = {"result": ret}
                except Exception as exc:
                    result = {"error": str(exc), "exc_type": type(exc).__name__}

            self._send_framed(conn, pickle.dumps(result))
        except Exception as exc:
            logger.debug("Bridge handle error: %s", exc)
        finally:
            conn.close()

    def stop(self) -> None:
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


# ---------------------------------------------------------------------------
# Kernel-side bridge stub code
# ---------------------------------------------------------------------------

_BRIDGE_BOOTSTRAP = """\
import pickle as _gptme_pickle
import socket as _gptme_socket
import struct as _gptme_struct

_GPTME_BRIDGE_PORT = {port}
_GPTME_BRIDGE_TOKEN = bytes.fromhex('{token_hex}')

def _gptme_recv_exact(s, n):
    '''Read exactly n bytes, handling partial reads.'''
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)

def _gptme_rpc(fn_name, *args, **kwargs):
    '''Call a function in the gptme process via the bridge socket.'''
    with _gptme_socket.socket(_gptme_socket.AF_INET, _gptme_socket.SOCK_STREAM) as _s:
        _s.connect(('127.0.0.1', _GPTME_BRIDGE_PORT))
        _s.sendall(_GPTME_BRIDGE_TOKEN)
        _payload = _gptme_pickle.dumps({{'fn': fn_name, 'args': args, 'kwargs': kwargs}})
        _s.sendall(_gptme_struct.pack('>I', len(_payload)) + _payload)
        _raw = _gptme_recv_exact(_s, 4)
        (_length,) = _gptme_struct.unpack('>I', _raw)
        _buf = bytearray()
        while len(_buf) < _length:
            _chunk = _s.recv(_length - len(_buf))
            if not _chunk:
                break
            _buf.extend(_chunk)
    _result = _gptme_pickle.loads(bytes(_buf))
    if 'error' in _result:
        raise RuntimeError(f"Bridge call {{fn_name}} failed: {{_result['error']}}")
    return _result['result']
"""

_STUB_TEMPLATE = (
    "def {name}(*args, **kwargs): return _gptme_rpc('{name}', *args, **kwargs)"
)


# ---------------------------------------------------------------------------
# Kernel execution result
# ---------------------------------------------------------------------------


class KernelExecResult:
    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        result_text: str | None = None,
        error: dict | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.result_text = result_text
        self.error = error

    @property
    def had_error(self) -> bool:
        return self.error is not None


# ---------------------------------------------------------------------------
# KernelBackend
# ---------------------------------------------------------------------------


class KernelBackend:
    """Manages a Jupyter kernel for executing IPython code cells."""

    def __init__(self) -> None:
        self._km: Any = None  # KernelManager
        self._kc: Any = None  # KernelClient
        self._started = False
        self._bridge = BridgeServer()
        self._bridge.start()
        # All registered stub codes — retained so they can be re-pushed after a restart.
        self._registered_stubs: list[str] = []
        # Track last synced working directory; None forces a sync on first execute.
        self._last_cwd: str | None = None

    # ------------------------------------------------------------------
    # Environment detection
    # ------------------------------------------------------------------

    @staticmethod
    def _find_project_python() -> str | None:
        """Return the project venv's Python interpreter path, if usable."""
        from gptme.tools.python import _detect_venv

        venv = _detect_venv()
        if venv is None:
            return None

        # Unix
        candidate = venv / "bin" / "python"
        if candidate.exists():
            return str(candidate)
        # Windows
        candidate = venv / "Scripts" / "python.exe"
        if candidate.exists():
            return str(candidate)
        return None

    @staticmethod
    def _has_ipykernel(python_exe: str) -> bool:
        """Check if a Python interpreter has ipykernel installed."""
        result = subprocess.run(
            [python_exe, "-c", "import ipykernel"],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._started:
            if not self.is_alive():
                # Kernel has died; clean up so we restart below.
                logger.warning("Kernel has died; restarting")
                self._started = False
                self._kc = None
                self._km = None
                self._last_cwd = None  # force cwd re-sync after restart
            else:
                return

        # Restart the bridge server if it was stopped (e.g. after shutdown()).
        if self._bridge._sock is None:
            self._bridge.start()

        try:
            from jupyter_client import KernelManager
        except ImportError as exc:
            raise RuntimeError(
                "jupyter_client is required for kernel mode. "
                "Install with: pip install 'gptme[kernel]'"
            ) from exc

        project_python = self._find_project_python()
        extra_env: dict[str, str] = {}

        self._km = KernelManager()

        if project_python and self._has_ipykernel(project_python):
            logger.info("Starting kernel with project Python: %s", project_python)
            self._km.kernel_cmd = [
                project_python,
                "-m",
                "ipykernel_launcher",
                "-f",
                "{connection_file}",
            ]
        else:
            if project_python:
                logger.warning(
                    "Project Python %s lacks ipykernel; falling back to gptme's Python",
                    project_python,
                )
            else:
                logger.info("No project venv found; using gptme's Python for kernel")

        # Pass env only when we actually want to override something; passing
        # None causes jupyter_client's provisioner to crash (it calls .get on it).
        start_kwargs: dict = {"extra_arguments": []}
        if extra_env:
            start_kwargs["env"] = extra_env
        self._km.start_kernel(**start_kwargs)
        self._kc = self._km.client()
        self._kc.start_channels()
        try:
            self._kc.wait_for_ready(timeout=30)
        except RuntimeError as exc:
            self._km.shutdown_kernel(now=True)
            raise RuntimeError(f"Kernel failed to start: {exc}") from exc

        self._started = True
        logger.info("Kernel started")

        # Push bridge bootstrap with current port and auth token.
        self._exec_silent(
            _BRIDGE_BOOTSTRAP.format(
                port=self._bridge.port,
                token_hex=self._bridge._token.hex(),
            ),
            timeout=10,
        )

        # Push all registered stubs — handles both first start and restarts.
        for stub_code in self._registered_stubs:
            self._exec_silent(stub_code, timeout=5)

    def is_alive(self) -> bool:
        return self._started and self._km is not None and self._km.is_alive()

    def shutdown(self) -> None:
        if self._kc:
            try:
                self._kc.stop_channels()
            except Exception:
                pass
        if self._km and self._km.is_alive():
            try:
                self._km.shutdown_kernel(now=True)
            except Exception:
                pass
        self._bridge.stop()
        self._started = False
        self._kc = None
        self._km = None
        self._last_cwd = None
        # _registered_stubs intentionally kept so they are re-pushed on the next start.

    # ------------------------------------------------------------------
    # Bridge function registration
    # ------------------------------------------------------------------

    def register_bridge_function(self, fn: Callable) -> None:
        """Register fn in the bridge; push a stub into the kernel namespace."""
        self._bridge.register(fn)
        stub_code = _STUB_TEMPLATE.format(name=fn.__name__)
        # Retain for re-push on restart.
        self._registered_stubs.append(stub_code)
        if self._started:
            self._exec_silent(stub_code, timeout=5)

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------

    def _exec_silent(self, code: str, timeout: float = 10) -> None:
        """Execute code silently (no output capture, for setup)."""
        msg_id = self._kc.execute(code, silent=True, store_history=False)
        deadline = _monotonic() + timeout
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                logger.warning("Silent exec timed out: %.60s", code)
                return
            try:
                msg = self._kc.get_shell_msg(timeout=min(remaining, 1.0))
            except Exception:
                continue
            if msg["parent_header"].get("msg_id") == msg_id:
                return

    def execute(self, code: str, timeout: float = 120) -> KernelExecResult:
        """Execute a code cell and return captured outputs."""
        self._ensure_started()

        # Sync working directory so the kernel and gptme stay in the same folder.
        cwd = os.getcwd()
        if cwd != self._last_cwd:
            self._exec_silent(
                f"import os as _gptme_os; _gptme_os.chdir({cwd!r})",
                timeout=5,
            )
            self._last_cwd = cwd

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_bytes = 0
        stderr_bytes = 0
        stdout_total_bytes = 0  # total received, including dropped chunks
        stderr_total_bytes = 0
        result_text: str | None = None
        error: dict | None = None

        msg_id = self._kc.execute(code, silent=False, store_history=True)
        deadline = _monotonic() + timeout

        timed_out = False
        while True:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                logger.warning("Kernel execute timed out after %.0fs", timeout)
                self.interrupt()
                timed_out = True
                break
            try:
                # Short poll so we don't block too long after an external interrupt
                msg = self._kc.get_iopub_msg(timeout=min(remaining, 0.5))
            except Exception:
                # No iopub message within the poll window; check shell for execute_reply
                try:
                    shell_msg = self._kc.get_shell_msg(timeout=0)
                    if shell_msg["parent_header"].get("msg_id") == msg_id:
                        break
                except Exception:
                    pass
                continue

            if msg["parent_header"].get("msg_id") != msg_id:
                continue

            msg_type: str = msg["msg_type"]
            content: dict = msg["content"]

            if msg_type == "stream":
                name = content.get("name", "")
                text = content.get("text", "")
                if name == "stdout":
                    chunk_bytes = len(text.encode("utf-8", errors="replace"))
                    stdout_total_bytes += chunk_bytes
                    if stdout_bytes < _MAX_STREAMING_OUTPUT_BYTES:
                        stdout_parts.append(text)
                        stdout_bytes += chunk_bytes
                elif name == "stderr":
                    chunk_bytes = len(text.encode("utf-8", errors="replace"))
                    stderr_total_bytes += chunk_bytes
                    if stderr_bytes < _MAX_STREAMING_OUTPUT_BYTES:
                        stderr_parts.append(text)
                        stderr_bytes += chunk_bytes

            elif msg_type == "execute_result":
                data = content.get("data", {})
                result_text = data.get("text/plain")

            elif msg_type == "error":
                import re

                tb_lines = content.get("traceback", [])
                clean_tb = [
                    re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line) for line in tb_lines
                ]
                error = {
                    "ename": content.get("ename", ""),
                    "evalue": content.get("evalue", ""),
                    "traceback": clean_tb,
                }

            elif msg_type == "status" and content.get("execution_state") == "idle":
                break

        # After a timeout-interrupt we must drain iopub until idle so the kernel
        # is in a clean state for the next execute() call.
        if timed_out:
            self._drain_until_idle(grace=5.0)
            # Report the timeout explicitly so the model knows the cell did not finish.
            if error is None:
                error = {
                    "ename": "ExecutionTimeout",
                    "evalue": f"Cell execution timed out after {timeout:.0f}s and was interrupted",
                    "traceback": [],
                }

        def _with_marker(parts: list[str], buffered: int, total: int) -> str:
            text = "".join(parts)
            if total > buffered:
                dropped = total - buffered
                text += (
                    f"\n\n[... {dropped:,} bytes omitted "
                    f"({total:,} total — capture limit reached, head only) ...]"
                )
            return text

        return KernelExecResult(
            stdout=_with_marker(stdout_parts, stdout_bytes, stdout_total_bytes),
            stderr=_with_marker(stderr_parts, stderr_bytes, stderr_total_bytes),
            result_text=result_text,
            error=error,
        )

    def _drain_until_idle(self, grace: float = 5.0) -> None:
        """Consume iopub messages until the kernel reports idle or grace elapses."""
        deadline = _monotonic() + grace
        while _monotonic() < deadline:
            try:
                msg = self._kc.get_iopub_msg(timeout=min(deadline - _monotonic(), 0.5))
            except Exception:
                break
            if (
                msg.get("msg_type") == "status"
                and msg.get("content", {}).get("execution_state") == "idle"
            ):
                break

    def interrupt(self) -> None:
        """Send SIGINT to the kernel, interrupting any blocking operation."""
        if self._km and self._km.is_alive():
            logger.info("Interrupting kernel")
            self._km.interrupt_kernel()


def _monotonic() -> float:
    import time

    return time.monotonic()


# ---------------------------------------------------------------------------
# Module-level backend singleton
# ---------------------------------------------------------------------------

_backend: KernelBackend | None = None


def get_backend() -> KernelBackend:
    global _backend
    if _backend is None:
        _backend = KernelBackend()
    return _backend


def reset_backend() -> None:
    """Shut down and discard the current backend (for tests / clean shutdown)."""
    global _backend
    if _backend is not None:
        try:
            _backend.shutdown()
        except Exception:
            pass
        _backend = None
