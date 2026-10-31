"""
Jupyter kernel backend for the IPython tool.

Runs IPython cells in a separate kernel process, enabling:
- Reliable interruption via SIGINT (even for C-extension blocking calls)
- Project environment isolation (%pip install goes to project env)
- Process isolation (kernel crash doesn't take down gptme)

Activated automatically when a project environment is detected (auto mode),
or explicitly with GPTME_IPYTHON_BACKEND=kernel.

The kernel is started lazily on first use and restarted on crash.
A socket-based bridge lets kernel-side code call registered gptme
functions. Message-returning helpers deliver their attachments directly back
to gptme rather than reducing them to a kernel text representation.
"""

import base64
import binascii
import contextlib
import io
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
from dataclasses import dataclass
from pathlib import Path
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
_MAX_RASTER_BYTES = 5 * 1024 * 1024
_MAX_RASTER_TOTAL_BYTES = 20 * 1024 * 1024
_MAX_RASTER_IMAGES = 16
_MAX_RASTER_PIXELS = 16_000_000
_MAX_TEXT_DISPLAY_SLOTS = 1024  # bound display-ID metadata independently of rasters
_RASTER_FORMATS = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}


@dataclass(frozen=True)
class KernelRaster:
    data: bytes
    mime_type: str
    output_type: str


@dataclass
class _RasterBudget:
    attempts: int = 0
    decoded_bytes: int = 0


def _collect_raster(
    data: dict,
    output_type: str,
    images: list[KernelRaster],
    budget: _RasterBudget | None = None,
) -> str | None:
    """Retain one supported representation per output, bounded before decoding.

    The Jupyter message has already been received; these limits bound additional
    host decoding/storage, not the kernel's memory or the transport frame itself.
    """
    mime = next((mime for mime in _RASTER_FORMATS if mime in data), None)
    if mime is None:
        return (
            "Unsupported kernel image output omitted."
            if any(key.startswith("image/") for key in data)
            else None
        )
    if budget is None:
        budget = _RasterBudget(len(images), sum(len(image.data) for image in images))
    if budget.attempts >= _MAX_RASTER_IMAGES:
        return "Kernel image count limit reached; additional images omitted."
    remaining = _MAX_RASTER_TOTAL_BYTES - budget.decoded_bytes
    limit = min(_MAX_RASTER_BYTES, remaining)
    encoded = data[mime]
    if not isinstance(encoded, str):
        return "Invalid kernel raster payload omitted."
    if len(encoded) > 4 * ((limit + 2) // 3):
        return "Kernel image byte limit reached; oversized image omitted."
    # Charge failed validation too; clears/updates never reopen this allowance.
    decoded_bound = ((len(encoded) + 3) // 4) * 3 - min(
        len(encoded) - len(encoded.rstrip("=")), 2
    )
    if remaining <= 0 or decoded_bound > limit:
        return "Kernel image byte limit reached; oversized image omitted."
    budget.attempts += 1
    budget.decoded_bytes += decoded_bound
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return "Invalid kernel raster base64 omitted."
    if len(decoded) > limit:
        return "Kernel image byte limit reached; oversized image omitted."
    # Validate the advertised format and bound decompression before loading pixels.
    # Pillow is imported lazily so ordinary kernel text pays no imaging startup cost.
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(decoded)) as image:
            if image.format != _RASTER_FORMATS[mime]:
                return "Kernel raster MIME does not match its bytes; image omitted."
            if image.width * image.height > _MAX_RASTER_PIXELS:
                return "Kernel image pixel limit reached; image omitted."
            image.verify()
        # verify() checks structural integrity but does not decompress pixels.
        with Image.open(io.BytesIO(decoded)) as image:
            image.load()
    except (
        OSError,
        ValueError,
        SyntaxError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
    ):
        return "Invalid kernel raster image omitted."
    images.append(KernelRaster(decoded, mime, output_type))
    return None


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
                        if (
                            fn_name != "_gptme_deliver_messages"
                            and call.get("cwd", os.getcwd()) != os.getcwd()
                        ):
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

# Wrap ip.displayhook to intercept gptme Message / list[Message] cell results
# and deliver them back to gptme via the bridge.  This prevents the kernel
# from sending an execute_result with a useless repr while still surfacing the
# structured object on the gptme side (execute_python already routes Messages).
#
# ipykernel resets sys.displayhook = ip.displayhook before each cell, so
# patching sys.displayhook alone does not survive across cells.  We replace
# ip.displayhook with a wrapper that delegates everything except Messages; the
# reset then installs our wrapper into sys.displayhook automatically.
#
# Duck-typing avoids importing gptme.message in project kernels that may not
# have gptme installed.

_gptme_orig_hook = get_ipython().displayhook

class _GptmeDisplayHookWrapper:
    '''Thin proxy around ZMQShellDisplayHook that intercepts Message results.

    ipykernel's ZMQShellDisplayHook.set_parent() resets sys.displayhook = self
    (the original hook) before every cell execution. We intercept set_parent to
    repair sys.displayhook back to this wrapper immediately afterward.
    '''
    def __call__(self, value):
        if value is not None:
            _t = type(value)
            _tn = _t.__name__
            _mod = getattr(_t, '__module__', '')
            _msgs = None
            if _tn == 'Message' and 'gptme' in _mod:
                _msgs = [value]
            elif _tn == 'list' and value:
                if all(type(_m).__name__ == 'Message' and 'gptme' in getattr(type(_m), '__module__', '') for _m in value):
                    _msgs = list(value)
            if _msgs is not None:
                try:
                    _gptme_rpc('_gptme_deliver_messages', _msgs)
                    return
                except Exception:
                    # Bridge unavailable/busy: fall back to the normal repr so a
                    # successful Message-returning cell does not become an error.
                    pass
        _gptme_orig_hook(value)
    def set_parent(self, parent):
        import sys as _gptme_sys2
        _gptme_orig_hook.set_parent(parent)   # this resets sys.displayhook = orig
        _gptme_sys2.displayhook = self         # repair: restore wrapper
    def __getattr__(self, name):
        return getattr(_gptme_orig_hook, name)
    def __setattr__(self, name, value):
        if name.startswith('_GptmeDisplayHookWrapper__') or name == '_gptme_orig_hook':
            object.__setattr__(self, name, value)
        else:
            setattr(_gptme_orig_hook, name, value)

import sys as _gptme_sys
_gptme_ip = get_ipython()
_gptme_hook_wrapper = _GptmeDisplayHookWrapper()
# Replace all three references IPython/ipykernel uses so none can revert
# sys.displayhook back to the original before or during a cell execution:
#   ip.displayhook     — used by ipykernel for set_parent / publish
#   ip.display_trap.hook — installed as sys.displayhook inside DisplayTrap.set()
#   sys.displayhook    — used by Python's exec for expression results
_gptme_ip.displayhook = _gptme_hook_wrapper
_gptme_ip.display_trap.hook = _gptme_hook_wrapper
_gptme_sys.displayhook = _gptme_hook_wrapper
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
        images: list[KernelRaster] | None = None,
        image_notices: list[str] | None = None,
    ) -> None:
        self.images = images or []
        self.image_notices = image_notices or []
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

    @staticmethod
    def _find_uv() -> str | None:
        """Return the path to the uv executable, or None if not found."""
        import shutil

        return shutil.which("uv")

    @staticmethod
    def _is_uv_project() -> bool:
        """Return True if the current directory looks like a uv-managed project.

        Requires an explicit uv marker — a ``uv.lock`` file or a ``[tool.uv]``
        section in ``pyproject.toml``. A plain pip project that merely has a
        ``pyproject.toml`` must not be treated as uv-managed: ``uv run`` would
        then create a ``.venv`` in a project the user never opted into uv.
        """
        import tomlkit
        from tomlkit.exceptions import ParseError

        cwd = Path.cwd()
        if (cwd / "uv.lock").exists():
            return True
        pyproject = cwd / "pyproject.toml"
        if not pyproject.exists() or (cwd / "poetry.lock").exists():
            return False
        if KernelBackend._find_uv() is None:
            return False
        try:
            config = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ParseError):
            return False
        tool = config.get("tool")
        return isinstance(tool, dict) and isinstance(tool.get("uv"), dict)

    @staticmethod
    def _build_uv_kernel_cmd() -> list[str] | None:
        """Build a kernel command using 'uv run --with ipykernel'.

        uv injects ipykernel ephemerally. --no-sync leaves the project's lock
        file and installed packages untouched, even with a missing/stale lock.
        --active respects the environment selected through VIRTUAL_ENV.

        Returns None if uv is not available.
        """
        uv = KernelBackend._find_uv()
        if uv is None:
            return None
        return [
            uv,
            "run",
            "--no-sync",
            "--active",
            "--with",
            "ipykernel",
            "python",
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ]

    def _set_kernel_cmd(self, cmd: list[str]) -> None:
        """Point the kernel launch at *cmd*.

        jupyter_client 8.x builds the launch command from ``kernel_spec.argv``
        (via ``KernelManager.format_kernel_cmd``); assigning
        ``KernelManager.kernel_cmd`` is silently ignored, so the default kernel
        spec would launch instead. Mutate the spec's argv so the intended
        interpreter/environment is actually used.
        """
        assert self._km is not None
        kernel_spec = self._km.kernel_spec
        if kernel_spec is None:
            # No kernelspec resolved (empty kernel_name). Surface a clear error
            # rather than silently launching the wrong interpreter.
            raise RuntimeError(
                "Cannot set kernel command: no kernel spec is configured"
            )
        kernel_spec.argv = list(cmd)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._started:
            if not self.is_alive():
                # Kernel has died; clean up so we restart below.
                logger.warning("Kernel has died; restarting")
                if self._kc is not None:
                    # Stop the dead client's channels before discarding it, so
                    # repeated crashes don't leak sockets and heartbeat threads.
                    with contextlib.suppress(Exception):
                        self._kc.stop_channels()
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
        # Snapshot the default launch command so a failed uv-based launch can
        # fall back to gptme's Python instead of regressing to a hard error.
        default_kernel_argv = (
            list(self._km.kernel_spec.argv) if self._km.kernel_spec else None
        )
        using_uv = False

        if project_python and self._has_ipykernel(project_python):
            logger.info("Starting kernel with project Python: %s", project_python)
            self._set_kernel_cmd(
                [
                    project_python,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                ]
            )
        elif project_python or self._is_uv_project():
            # Project venv found but lacks ipykernel, or this is a uv project
            # without an activated venv.  Try `uv run --with ipykernel` so the
            # kernel runs inside the project's environment without requiring the
            # user to manually add ipykernel as a dependency.
            uv_cmd = self._build_uv_kernel_cmd()
            if uv_cmd:
                logger.info(
                    "Project Python lacks ipykernel; using 'uv run --with ipykernel'"
                )
                self._set_kernel_cmd(uv_cmd)
                using_uv = True
            else:
                if project_python:
                    logger.warning(
                        "Project Python %s lacks ipykernel and uv is not available; "
                        "falling back to gptme's Python",
                        project_python,
                    )
                else:
                    logger.info(
                        "uv project detected but uv is not available; "
                        "using gptme's Python for kernel"
                    )
        else:
            logger.info("No project venv found; using gptme's Python for kernel")

        # Pass env only when we actually want to override something; passing
        # None causes jupyter_client's provisioner to crash (it calls .get on it).
        start_kwargs: dict = {"extra_arguments": []}
        if extra_env:
            start_kwargs["env"] = extra_env
        try:
            self._km.start_kernel(**start_kwargs)
            self._kc = self._km.client()
            self._kc.start_channels()
            self._kc.wait_for_ready(timeout=30)
        except Exception as exc:
            if self._kc is not None:
                # Stop the failed client's channels before replacing it, so
                # repeated failures don't leak sockets and heartbeat threads.
                with contextlib.suppress(Exception):
                    self._kc.stop_channels()
                self._kc = None
            with contextlib.suppress(Exception):
                self._km.shutdown_kernel(now=True)
            if using_uv and default_kernel_argv is not None:
                # The uv path can fail (e.g. an offline environment with no warm
                # uv cache). Retry with the default spec so a working kernel is
                # still available, matching pre-fallback behaviour.
                logger.warning(
                    "'uv run --with ipykernel' failed to start the kernel (%s); "
                    "falling back to gptme's Python",
                    exc,
                )
                self._km = KernelManager()
                if self._km.kernel_spec is not None:
                    self._km.kernel_spec.argv = default_kernel_argv
                try:
                    self._km.start_kernel(**start_kwargs)
                    self._kc = self._km.client()
                    self._kc.start_channels()
                    self._kc.wait_for_ready(timeout=30)
                except Exception as exc2:
                    if self._kc is not None:
                        with contextlib.suppress(Exception):
                            self._kc.stop_channels()
                        self._kc = None
                    with contextlib.suppress(Exception):
                        self._km.shutdown_kernel(now=True)
                    raise RuntimeError(f"Kernel failed to start: {exc2}") from exc2
            else:
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

        # Register the special message-delivery bridge handler (server-side only;
        # no kernel stub is pushed — the displayhook calls it by name directly).
        bridge = self._bridge

        def _gptme_deliver_messages(msgs: list) -> None:
            bridge.messages.extend(msgs)

        self._bridge.register(_gptme_deliver_messages)

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

    def execute(self, code: str, timeout: float | None = 120) -> KernelExecResult:
        """Execute a code cell and return captured outputs; None disables timeout."""
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
        images: list[KernelRaster] = []
        image_notices: list[str] = []
        # Cumulative decoding is separate from bounded visible output slots.
        raster_budget = _RasterBudget()
        visible_images: list[tuple[str | None, KernelRaster | None]] = []
        clear_images_pending = False

        def collect_output(content: dict, output_type: str) -> None:
            display_id = content.get("transient", {}).get("display_id")
            if not isinstance(display_id, str) or not display_id:
                display_id = None
            updating = output_type == "update_display_data"
            matches = [
                index
                for index, (identity, _) in enumerate(visible_images)
                if display_id is not None and identity == display_id
            ]
            # Earlier cells' attachments are already sent; don't invent a slot.
            if updating and not matches:
                return
            previous_count = len(images)
            notice = _collect_raster(
                content.get("data", {}), output_type, images, raster_budget
            )
            if notice and notice not in image_notices:
                image_notices.append(notice)
            image = images[-1] if len(images) > previous_count else None
            if updating:
                for index in matches:
                    visible_images[index] = (display_id, image)
            elif image is not None:
                # Decoding already bounds actual images. Text-only display IDs
                # must not consume their allowance or block a later plot.
                visible_images.append((display_id, image))
            elif display_id is not None:
                text_slots = sum(image is None for _, image in visible_images)
                if text_slots < _MAX_TEXT_DISPLAY_SLOTS:
                    visible_images.append((display_id, None))
                else:
                    notice = "Kernel text display-ID tracking limit reached; additional IDs omitted."
                    if notice not in image_notices:
                        image_notices.append(notice)

        self._bridge.cell_context = copy_context()
        msg_id = self._kc.execute(code, silent=False, store_history=True)
        self._bridge.active_cell = msg_id
        deadline = _monotonic() + timeout if timeout is not None else None

        timed_out = False
        died = False
        while True:
            remaining = deadline - _monotonic() if deadline is not None else 0.5
            if deadline is not None and remaining <= 0:
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

            if msg_type == "clear_output":
                if content.get("wait", False):
                    clear_images_pending = True
                else:
                    visible_images.clear()
                    clear_images_pending = False
                continue
            if clear_images_pending and msg_type in (
                "stream",
                "execute_result",
                "display_data",
                "error",
            ):
                visible_images.clear()
                clear_images_pending = False

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
                collect_output(content, msg_type)
                text = data.get("text/plain")
                result_text = _cap_kernel_text(text) if text is not None else None

            elif msg_type in ("display_data", "update_display_data"):
                # explicit display() calls — collect as stdout so they aren't silently dropped
                data = content.get("data", {})
                collect_output(content, msg_type)
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

        # An update can expand one decoded raster into many visible slots.
        # Bound the expanded output too: each slot becomes a retained file.
        retained_images: list[KernelRaster] = []
        retained_bytes = 0
        for _, image in visible_images:
            if image is None:
                continue
            notice = None
            if len(retained_images) >= _MAX_RASTER_IMAGES:
                notice = "Kernel image count limit reached; additional images omitted."
            elif retained_bytes + len(image.data) > _MAX_RASTER_TOTAL_BYTES:
                notice = "Kernel image byte limit reached; additional images omitted."
            if notice is not None:
                if notice not in image_notices:
                    image_notices.append(notice)
                continue
            retained_images.append(image)
            retained_bytes += len(image.data)

        self._bridge.active_cell = None
        return KernelExecResult(
            stdout=_with_marker(stdout_parts, stdout_bytes, stdout_total_bytes),
            stderr=_with_marker(stderr_parts, stderr_bytes, stderr_total_bytes),
            result_text=result_text,
            error=error,
            images=retained_images,
            image_notices=image_notices,
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
