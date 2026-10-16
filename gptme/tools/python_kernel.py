"""
Jupyter kernel backend for the IPython tool.

Runs IPython cells in a separate kernel process, enabling:
- Reliable interruption via SIGINT (even for C-extension blocking calls)
- Project environment isolation (%pip install goes to project env)
- Process isolation (kernel crash doesn't take down gptme)

Enable with: GPTME_IPYTHON_BACKEND=kernel

The kernel is started lazily on first use and restarted on crash.
A socket-based bridge lets kernel-side code call registered gptme
functions. Message-returning helpers deliver their attachments directly back
to gptme rather than reducing them to a kernel text representation.
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
from contextvars import Context, copy_context
from typing import Any

from .shell_memory import Target as MemoryTarget
from .shell_memory import group_alive, kill_group
from .shell_memory import watch as watch_memory

logger = logging.getLogger(__name__)

_MAX_BRIDGE_CONNECTIONS = 16
_MAX_BRIDGE_MESSAGE_BYTES = 64 * 1024 * 1024  # bound host memory per RPC frame
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
        # 128-bit per-instance capability, verified before unpickling. It excludes
        # unauthenticated local users, not same-user debuggers or the kernel itself.
        # The kernel executes trusted user code; this bridge is not a sandbox.
        self._token: bytes = secrets.token_bytes(16)
        # Bound semaphore limits concurrent handler threads, preventing fd exhaustion.
        self._semaphore = threading.BoundedSemaphore(_MAX_BRIDGE_CONNECTIONS)
        self._call_lock = threading.Lock()
        self.messages: list[Any] = []
        self.active_cell: str | None = None
        self.cell_context = Context()
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()

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
            with self._connections_lock:
                if self._sock is None:
                    conn.close()
                    self._semaphore.release()
                    break
                self._connections.add(conn)
            t = threading.Thread(
                target=self._handle_with_release, args=(conn,), daemon=True
            )
            t.start()

    def _handle_with_release(self, conn: socket.socket) -> None:
        try:
            self._handle(conn)
        finally:
            with self._connections_lock:
                self._connections.discard(conn)
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
        if length > _MAX_BRIDGE_MESSAGE_BYTES:
            raise ValueError("Bridge frame too large")
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
                    from gptme.message import Message

                    # Helpers run in the host process: never change its global cwd
                    # for work that can outlive an interrupted kernel cell.
                    with self._call_lock:
                        if call.get("cwd", os.getcwd()) != os.getcwd():
                            raise RuntimeError(
                                "Bridge helper working directory differs from the host; "
                                "change directory through the shell before calling helpers"
                            )
                        cell = call.get("cell")
                        if cell is not None and cell != self.active_cell:
                            raise RuntimeError(
                                "Bridge call belongs to an inactive cell"
                            )
                        # Handler threads do not inherit ContextVars. Copy the
                        # cell caller's context for each helper so session policy
                        # (including browser host restrictions) is preserved,
                        # without leaking a helper's changes into later calls.
                        ret = self.cell_context.copy().run(fn, *args, **kwargs)
                        if cell is None or cell == self.active_cell:
                            if isinstance(ret, Message):
                                self.messages.append(ret)
                            elif isinstance(ret, list) and all(
                                isinstance(m, Message) for m in ret
                            ):
                                self.messages.extend(ret)
                    # Project kernels need not have gptme installed. Return a
                    # portable attribute-bearing value while retaining originals
                    # (including attachments) in the host output buffer.
                    from types import SimpleNamespace

                    def portable(value: Any) -> Any:
                        if isinstance(value, Message):
                            return SimpleNamespace(**vars(value))
                        if isinstance(value, list):
                            return [portable(item) for item in value]
                        return value

                    result = {"result": portable(ret)}
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
        with self._connections_lock:
            for conn in self._connections:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
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
import os as _gptme_os

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
        _cell = get_ipython().kernel.get_parent().get('header', {{}}).get('msg_id')
        _payload = _gptme_pickle.dumps({{'fn': fn_name, 'args': args, 'kwargs': kwargs, 'cwd': _gptme_os.getcwd(), 'cell': _cell}})
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
        # Explanation from the memory guard after it killed the kernel; reported
        # to the model once, with the next cell's output.
        self._memory_notice: str | None = None

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

        try:
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
        except RuntimeError:
            self.shutdown()
            raise

        # Register the guard only once the kernel is up and bootstrapped, so a
        # failed start never leaves _started set with a half-initialized kernel.
        self._watch_memory()

    def _watch_memory(self) -> None:
        """Have the shell memory guard watch the kernel's process group.

        jupyter_client starts the kernel in its own session, so the group is the
        kernel plus anything a cell spawns. If memory runs low the guard may kill
        it like any other agent-started group (see shell_memory.py).
        """
        # The guard is POSIX-only (it reads /proc and kills process groups), and
        # os.getpgid is Unix-only; return before touching process groups so the
        # first cell on Windows cannot raise AttributeError here.
        if os.name != "posix":
            return
        km = self._km
        provisioner = getattr(km, "provisioner", None)
        pid = getattr(provisioner, "pid", None)
        if not pid:
            return
        pgid = getattr(provisioner, "pgid", None)
        if not pgid:
            try:
                pgid = os.getpgid(pid)
            except OSError:
                return
        if pgid == os.getpgid(0):
            # Never register our own group: killing it would take gptme down.
            logger.debug("Kernel shares gptme's process group; not guarding it")
            return
        watch_memory(
            MemoryTarget(
                pgid=pgid,
                label="the IPython kernel",
                kill=lambda: kill_group(pgid),
                # Watch the whole group, not just the kernel leader: a cell can
                # spawn workers that outlive a kernel crash or kill. With
                # km.is_alive the guard would drop the target (and stop watching
                # those workers) as soon as the leader exits.
                alive=lambda: group_alive(pgid),
                on_killed=lambda reason: self._on_memory_killed(reason, km),
            )
        )

    def _on_memory_killed(self, reason: str, km: Any) -> None:
        # Old groups can retain workers after a restart. Bind the notice to the
        # manager that owned the group, not whichever kernel is running now.
        if self._km is km:
            notice = (
                f"{reason} The kernel restarts with the next cell; variables and "
                "imports from earlier cells are lost."
            )
        else:
            notice = (
                f"{reason} This was an earlier kernel's remaining workers; "
                "the current kernel state is unchanged."
            )
        existing = self._memory_notice
        self._memory_notice = notice if not existing else existing + "\n" + notice

    def _take_memory_notice(self) -> str | None:
        notice, self._memory_notice = self._memory_notice, None
        return notice

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
                self.interrupt()
                raise RuntimeError("Kernel setup timed out; cell was not executed")
            try:
                msg = self._kc.get_shell_msg(timeout=min(remaining, 1.0))
            except Exception:
                if not self.is_alive():
                    raise RuntimeError("Kernel process exited during setup") from None
                continue
            if msg["parent_header"].get("msg_id") == msg_id:
                content = msg.get("content", {})
                if content.get("status") == "error":
                    raise RuntimeError(
                        f"Kernel setup failed: {content.get('ename')}: "
                        f"{content.get('evalue')}"
                    )
                return

    def execute(self, code: str, timeout: float = 120) -> KernelExecResult:
        """Execute a code cell and return captured outputs."""
        self._ensure_started()

        # A previous cell may itself have used %cd/os.chdir, so synchronize on
        # every execution, not only when the host directory changes.
        cwd = os.getcwd()
        self._exec_silent(
            f"import os as _gptme_os; _gptme_os.chdir({cwd!r})",
            timeout=5,
        )
        self._last_cwd = cwd
        self._bridge.messages.clear()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        stdout_bytes = 0
        stderr_bytes = 0
        stdout_total_bytes = 0  # total received, including dropped chunks
        stderr_total_bytes = 0
        result_text: str | None = None
        error: dict | None = None

        self._bridge.cell_context = copy_context()
        msg_id = self._kc.execute(code, silent=False, store_history=True)
        self._bridge.active_cell = msg_id
        deadline = _monotonic() + timeout

        timed_out = False
        died = False
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
                # No iopub message within the poll window. A kernel killed by
                # the memory guard, OOM or a crash sends nothing: check it is
                # still there rather than waiting for the timeout.
                if not self.is_alive():
                    logger.warning("Kernel process exited while running a cell")
                    died = True
                    break
                # Check shell for execute_reply
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
                        retained = text.encode("utf-8", errors="replace")[
                            : _MAX_STREAMING_OUTPUT_BYTES - stdout_bytes
                        ].decode("utf-8", errors="ignore")
                        stdout_parts.append(retained)
                        stdout_bytes += len(retained.encode("utf-8"))
                elif name == "stderr":
                    chunk_bytes = len(text.encode("utf-8", errors="replace"))
                    stderr_total_bytes += chunk_bytes
                    if stderr_bytes < _MAX_STREAMING_OUTPUT_BYTES:
                        retained = text.encode("utf-8", errors="replace")[
                            : _MAX_STREAMING_OUTPUT_BYTES - stderr_bytes
                        ].decode("utf-8", errors="ignore")
                        stderr_parts.append(retained)
                        stderr_bytes += len(retained.encode("utf-8"))

            elif msg_type == "execute_result":
                data = content.get("data", {})
                text = data.get("text/plain")
                result_text = _cap_kernel_text(text) if text is not None else None

            elif msg_type == "display_data":
                # explicit display() calls — collect as stdout so they aren't silently dropped
                data = content.get("data", {})
                text = data.get("text/plain", "")
                if text:
                    chunk_bytes = len(text.encode("utf-8", errors="replace"))
                    stdout_total_bytes += chunk_bytes
                    if stdout_bytes < _MAX_STREAMING_OUTPUT_BYTES:
                        retained = text.encode("utf-8", errors="replace")[
                            : _MAX_STREAMING_OUTPUT_BYTES - stdout_bytes
                        ].decode("utf-8", errors="ignore")
                        stdout_parts.append(retained + "\n")
                        stdout_bytes += len(retained.encode("utf-8"))

            elif msg_type == "error":
                import re

                tb_lines = content.get("traceback", [])
                clean_tb = [
                    re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line) for line in tb_lines
                ]
                error = {
                    "ename": content.get("ename", ""),
                    "evalue": _cap_kernel_text(content.get("evalue", "")),
                    "traceback": [_cap_kernel_text("\n".join(clean_tb))],
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

        if died and error is None:
            error = {
                "ename": "KernelDied",
                "evalue": self._take_memory_notice()
                or (
                    "The kernel process exited while running this cell (crash, "
                    "out-of-memory kill or external signal). It restarts with "
                    "the next cell; variables and imports from earlier cells "
                    "are lost."
                ),
                "traceback": [],
            }

        # A guard kill between cells: the kernel restarted in _ensure_started,
        # so tell the model why its state is gone.
        if (notice := self._take_memory_notice()) is not None:
            stderr_parts.append(("\n" if stderr_parts else "") + notice + "\n")

        def _with_marker(parts: list[str], buffered: int, total: int) -> str:
            text = "".join(parts)
            if total > buffered:
                dropped = total - buffered
                text += (
                    f"\n\n[... {dropped:,} bytes omitted "
                    f"({total:,} total — capture limit reached, head only) ...]"
                )
            return text

        self._bridge.active_cell = None
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
        self._bridge.active_cell = None
        if self._km and self._km.is_alive():
            logger.info("Interrupting kernel")
            self._km.interrupt_kernel()


def _cap_kernel_text(text: str) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= _MAX_STREAMING_OUTPUT_BYTES:
        return text
    retained = data[:_MAX_STREAMING_OUTPUT_BYTES].decode("utf-8", errors="ignore")
    return (
        retained
        + f"\n[... {len(data) - len(retained.encode('utf-8')):,} bytes omitted ...]"
    )


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
