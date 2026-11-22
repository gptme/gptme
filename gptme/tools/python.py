"""
The assistant can execute Python code blocks.

It uses IPython to do so, and persists the IPython instance between calls to give a REPL-like experience.

Configuration:
    GPTME_IPYTHON_TIMEOUT: Seconds a code block may run before it is
        interrupted (default 600, 0 disables). Processes the block started are
        killed too, and ``time.sleep`` is interruptible while a block runs;
        other blocking calls (e.g. a socket read) only stop once they return.

``GPTME_IPYTHON_BACKEND`` controls which execution backend is used for Python
cells (default ``auto``):

- ``auto``: use the kernel backend when a project environment is detected
  (a ``.venv`` directory, an active ``VIRTUAL_ENV``, or a ``uv.lock`` /
  ``[tool.uv]`` project marker) and the ``kernel`` extra is installed.
  Falls back to in-process when no environment or kernel dependencies are found.
- ``kernel``: always use the kernel backend.
- ``inprocess``: always use the in-process backend.

The kernel backend runs cells in a separate Jupyter kernel process, enabling
reliable SIGINT interrupts and project-environment isolation
(``%pip install`` goes to the project venv, not gptme's).
"""

import ast
import atexit
import dataclasses
import functools
import importlib.util
import io
import os
import re
import site
import sys
import tempfile
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from ..constants import DECLINED_CONTENT
from ..hooks import (
    ConfirmAction,
    StopPropagation,
    current_conversation_id,
    get_confirmation,
)
from ..message import Message
from ..util.context import md_codeblock
from ..util.interrupt import (
    clear_pending,
    interruptible_sleep,
    kill_processes,
    raise_in_thread,
    start_tracking,
    stop_tracking,
)
from ..util.workspace import current_workspace
from . import get_tools
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
    callable_signature,
)

if TYPE_CHECKING:
    from IPython.core.interactiveshell import (
        ExecutionResult,
        InteractiveShell,  # fmt: skip
    )

    from ..logmanager import LogManager
    from ..message import ArtifactDescriptor, MessageMetadata
    from ..util.output_storage import PrivateOutputFile
    from .python_kernel import KernelExecResult


logger = getLogger(__name__)

# "auto" | "kernel" | "inprocess"
_IPYTHON_BACKEND = os.environ.get("GPTME_IPYTHON_BACKEND", "auto").lower()

# In-process backend state. CLI sessions share the process-global `_ipython`;
# server conversations get one shell each, held in `_conversation_ipython` so
# SESSION_END can drop it.
_conversation_ipython: dict[str, Any] = {}
_conv_ipython_lock = threading.Lock()
# Serializes the temporary os.chdir around one in-process cell on the server.
# Held only for that cell; other threads can still observe the temporary cwd.
# Blocking waits (subagent join) must call suspend_inprocess_session_cwd() so
# child conversations can run Python without deadlocking the parent.
_inprocess_cwd_lock = threading.Lock()
_inprocess_cwd_tls = threading.local()

# Cached result of auto-detection; None means not yet determined.
_kernel_mode_cache: bool | None = None

# Workspace key (cwd, VIRTUAL_ENV) the cached result was computed for. The
# auto-detection probes (_detect_venv, _is_uv_project) depend on both, so the
# cache must be recomputed when either changes — otherwise a session that
# starts outside a project and later moves into one would stay on the
# in-process backend despite auto mode's contract.
_kernel_mode_cache_key: tuple[str, str | None] | None = None

# Set once a cell has executed with a chosen backend.  From then on the choice
# is frozen: the kernel and in-process backends hold separate Python state
# (variables, imports), so silently switching because the working directory
# changed would discard the user's session.  A directory change before the
# first cell still re-evaluates (auto mode's workspace contract).
_kernel_mode_frozen = False


def _project_kernel_available() -> bool:
    """Probe project markers and the optional kernel dependencies."""
    from .python_kernel import KernelBackend

    has_venv = _detect_venv() is not None
    is_uv = KernelBackend._is_uv_project()
    available = all(
        importlib.util.find_spec(name) is not None
        for name in ("jupyter_client", "ipykernel")
    )
    return (has_venv or is_uv) and available


def _should_use_kernel() -> bool:
    """Return True if cells should run in a separate Jupyter kernel process.

    Explicit configuration always wins.  In auto mode the result is cached
    per workspace (cwd + VIRTUAL_ENV) so repeated cells in the same workspace
    don't redo the filesystem probes, while a workspace switch re-evaluates —
    until the first cell executes, after which the choice is frozen (see
    ``_kernel_mode_frozen``).
    """
    global _kernel_mode_cache, _kernel_mode_cache_key

    if _IPYTHON_BACKEND == "kernel":
        return True
    if _IPYTHON_BACKEND == "inprocess":
        return False

    if conversation_id := current_conversation_id.get():
        from .python_kernel import get_backend_choice

        choice = get_backend_choice(conversation_id)
        return choice if choice is not None else _project_kernel_available()

    # auto mode: once a cell has run, keep the backend stable even if the
    # working directory later changes.
    if _kernel_mode_frozen:
        return bool(_kernel_mode_cache)

    # auto mode: activate kernel when a project environment is found.
    # Any of these signals is enough: a .venv / VIRTUAL_ENV (even without a
    # working Python inside — the kernel will fall through to uv run), or a
    # uv-managed project (uv.lock / [tool.uv]).
    cache_key = (str(current_workspace()), os.environ.get("VIRTUAL_ENV"))
    if _kernel_mode_cache is None or _kernel_mode_cache_key != cache_key:
        _kernel_mode_cache = _project_kernel_available()
        _kernel_mode_cache_key = cache_key
        if _kernel_mode_cache:
            logger.info("Auto-activating kernel backend: project environment detected")
        else:
            logger.debug(
                "No usable project kernel environment found; using in-process IPython backend"
            )
    return _kernel_mode_cache


def _resolve_kernel_backend() -> bool:
    """Return the backend for a cell and freeze the choice for its conversation.

    ``execute_python`` calls this instead of ``_should_use_kernel`` so that
    once the first cell runs, a later ``os.chdir`` cannot flip backends and
    silently drop the session state held by the other interpreter.
    """
    global _kernel_mode_frozen
    use_kernel = _should_use_kernel()
    if conversation_id := current_conversation_id.get():
        from .python_kernel import freeze_backend_choice

        return freeze_backend_choice(conversation_id, use_kernel)
    _kernel_mode_frozen = True
    return use_kernel


# Default byte cap for python tool output (10 MiB)
_DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
# Headroom reserved in the capture buffer for the omission marker
_CAPTURE_MARKER_RESERVE = 256

_IMAGE_EXTS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".svg", ".gif", ".pdf"}
)
_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}


def _decode_slice(data: bytes) -> str:
    """Decode a byte slice that may cut a character in half.

    Tries surrogatepass first (round-trips lone surrogates); on a mid-character
    split falls back to dropping the partial bytes, which never expands the text.
    """
    try:
        return data.decode("utf-8", errors="surrogatepass")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


class CellTimeout(KeyboardInterrupt):
    """Raised in a cell's thread when it exceeds GPTME_IPYTHON_TIMEOUT."""


@contextmanager
def _inprocess_session_cwd() -> Generator[None, None, None]:
    """Pin one in-process cell to the session workspace, then restore.

    In-process IPython cannot scope cwd per conversation. Server conversations
    (those with ``set_workspace_cwd``) serialize cells under
    ``_inprocess_cwd_lock``, chdir for the cell, and restore the host cwd in
    ``finally``. CLI sessions have no workspace ContextVar and keep the
    process cwd, including cell-to-cell ``os.chdir`` / ``%cd``.

    The lock is held only for this cell, not a model turn. Other threads can
    still observe the temporary cwd while a cell runs (gptme/gptme#4951).
    """
    from ..util.workspace import get_workspace_cwd

    if get_workspace_cwd() is None:
        yield
        return

    target = current_workspace()
    with _inprocess_cwd_lock:
        host_cwd: Path | None
        try:
            host_cwd = Path.cwd()
        except FileNotFoundError:
            host_cwd = None
        try:
            os.chdir(target)
            _inprocess_cwd_tls.pin = {"host_cwd": host_cwd, "target": target}
            yield
        finally:
            _inprocess_cwd_tls.pin = None
            if host_cwd is not None:
                try:
                    os.chdir(host_cwd)
                except OSError as e:
                    logger.warning(
                        "Could not restore cwd to %s after in-process cell: %s",
                        host_cwd,
                        e,
                    )


@contextmanager
def suspend_inprocess_session_cwd() -> Generator[None, None, None]:
    """Release this thread's in-process cwd pin for a blocking wait.

    A server Python cell that calls ``subagent_parallel`` / ``subagent_wait``
    must not hold ``_inprocess_cwd_lock`` while child conversations run Python.
    No-op when this thread is not pinning. Child cells restore the host cwd
    before this resumes and re-pins ``current_workspace()``.
    """
    pin = getattr(_inprocess_cwd_tls, "pin", None)
    if pin is None:
        yield
        return

    try:
        cell_cwd = Path.cwd()
    except FileNotFoundError:
        cell_cwd = pin["target"]
    host_cwd = pin["host_cwd"]
    if host_cwd is not None:
        try:
            os.chdir(host_cwd)
        except OSError as e:
            logger.warning(
                "Could not restore cwd to %s before blocking wait: %s",
                host_cwd,
                e,
            )
    # Drop this cell's TeeIO so a nested conversation's capture does not wrap
    # it and leak that conversation's output into this cell's result.
    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    if isinstance(sys.stdout, TeeIO):
        sys.stdout = sys.stdout.original_stream
    if isinstance(sys.stderr, TeeIO):
        sys.stderr = sys.stderr.original_stream
    _inprocess_cwd_tls.pin = None
    _inprocess_cwd_lock.release()
    try:
        yield
    finally:
        _inprocess_cwd_lock.acquire()
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
        resume_host: Path | None
        try:
            resume_host = Path.cwd()
        except FileNotFoundError:
            resume_host = None
        try:
            os.chdir(cell_cwd)
        except OSError:
            os.chdir(current_workspace())
        _inprocess_cwd_tls.pin = {"host_cwd": resume_host, "target": cell_cwd}


def _get_timeout() -> float | None:
    """GPTME_IPYTHON_TIMEOUT in seconds (default 600); 0 disables."""
    raw = os.environ.get("GPTME_IPYTHON_TIMEOUT", "600")
    try:
        timeout = float(raw)
    except ValueError:
        logger.warning("Invalid GPTME_IPYTHON_TIMEOUT=%r, using 600s", raw)
        timeout = 600.0
    return timeout if timeout > 0 else None


@contextmanager
def _cell_timeout(timeout: float | None) -> Generator[Callable[[], bool], None, None]:
    """Interrupt the running cell after ``timeout`` seconds.

    Raises CellTimeout in this thread and kills the processes the cell
    started (unblocking e.g. ``subprocess.run``). Yields whether it fired.
    """
    fired = threading.Event()
    if timeout is None:
        yield fired.is_set
        return
    thread_id = threading.get_ident()
    started = start_tracking()  # only this cell's processes, not other threads'
    lock = threading.Lock()
    running = True

    def fire() -> None:
        with lock:
            if not running:
                return
            fired.set()
            raise_in_thread(thread_id, CellTimeout)
        kill_processes(set(started))

    timer = threading.Timer(timeout, fire)
    timer.daemon = True
    timer.start()
    try:
        yield fired.is_set
    except CellTimeout:
        pass  # landed outside the cell's code (IPython catches it inside)
    finally:
        with lock:
            running = False
            timer.cancel()
            clear_pending(thread_id)
        stop_tracking(started)


def _format_cell_output(stdout: str, stderr: str) -> str:
    output = ""
    if stdout:
        output += md_codeblock("stdout", _cap_output(stdout).rstrip()) + "\n\n"
    if stderr:
        output += md_codeblock("stderr", _cap_output(stderr).rstrip()) + "\n\n"
    return output


def _spill_full_output(output: str, command_info: str | None) -> Path | None:
    """Save *output* under the conversation's ``tool-outputs/``; best-effort.

    Returns None when no conversation directory is active or the write fails,
    so truncation still applies without the pointer.
    """
    try:
        from ..logmanager import LogManager
        from ..util.output_storage import save_large_output

        manager = LogManager.get_current_log()
        if manager is None or not manager.logdir:
            return None
        _, saved_path = save_large_output(
            content=output,
            logdir=manager.logdir,
            output_type="python",
            command_info=command_info,
        )
        return saved_path
    except Exception as e:  # saving must never break the truncation itself
        logger.warning("Could not save full python output: %s", e)
        return None


def _redact_cell_output(output: str, *, live_literals: list[str] | None = None) -> str:
    """Apply the shell's credential-redaction policy to python/kernel output.

    The python and kernel backends capture whatever a cell printed, including an
    accidental ``print(os.environ)`` or a dumped dotenv file. Shell and tmux
    output already passes this policy before it is shown inline or written to
    ``tool-outputs/``; python output did not, so a retained spill file could
    hold credentials the rest of the pipeline redacts. Same switch
    (``GPTME_SHELL_REDACT_SECRETS``) and same heuristic -- this is not a
    general secret scanner.
    """
    from ..config import get_config  # deferred: avoid import cycle

    if not get_config().get_env_bool("GPTME_SHELL_REDACT_SECRETS", True):
        return output
    from ..util.redact import redact_secret_values_from_output

    return redact_secret_values_from_output(output, live_literals=live_literals)[0]


def _cap_output(output: str, command_info: str | None = None) -> str:
    """Cap output to a reasonable size (10 MiB) with head+tail truncation.

    The cap is measured in UTF-8 bytes, not characters, so multibyte characters
    are counted correctly. When output is dropped and a conversation directory
    is active, the full text is saved under ``tool-outputs/`` and the omission
    marker names the file.

    Credential redaction runs before both the cap and the spill, so the
    retained file is the same redacted representation the model sees, and the
    reported byte counts describe that representation.
    """
    output = _redact_cell_output(output)
    # surrogatepass: valid Python strings may hold lone surrogates (e.g. from
    # surrogateescape-decoded filenames) that strict UTF-8 encoding rejects
    encoded = output.encode("utf-8", errors="surrogatepass")
    total_bytes = len(encoded)
    if total_bytes <= _DEFAULT_MAX_OUTPUT_BYTES:
        return output

    saved_path = _spill_full_output(output, command_info)
    saved_note = f"; full output saved to {saved_path}" if saved_path else ""

    # Output exceeds cap; return head+tail with truncation marker (byte-level slice)
    # Reserve room for the marker (sized with the widest numbers it can hold)
    # so the result stays within the cap
    worst_marker = f"\n\n[... {total_bytes:,} bytes omitted ({total_bytes:,} total{saved_note}) ...]\n\n"
    reserve = max(
        _CAPTURE_MARKER_RESERVE,
        len(worst_marker.encode("utf-8", errors="surrogatepass")),
    )
    half = (_DEFAULT_MAX_OUTPUT_BYTES - reserve) // 2
    head_bytes = encoded[:half]
    tail_bytes = encoded[-half:]
    omitted = total_bytes - len(head_bytes) - len(tail_bytes)
    head = _decode_slice(head_bytes)
    tail = _decode_slice(tail_bytes)
    return f"{head}\n\n[... {omitted:,} bytes omitted ({total_bytes:,} total{saved_note}) ...]\n\n{tail}"


def _snapshot_images(cwd: Path) -> dict[Path, float]:
    """Return a {path: mtime} snapshot of image files directly in *cwd*."""
    snapshot: dict[Path, float] = {}
    try:
        entries = list(cwd.iterdir())
    except OSError:
        return snapshot
    for p in entries:
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            try:
                snapshot[p] = p.stat().st_mtime
            except OSError:
                pass
    return snapshot


def _make_plot_artifacts(
    before: dict[Path, float], after: dict[Path, float]
) -> "list[ArtifactDescriptor]":
    """Return ArtifactDescriptors for image files created/modified since *before*."""
    descriptors: list[ArtifactDescriptor] = []
    for path, mtime in after.items():
        if path not in before or before[path] != mtime:
            descriptor: ArtifactDescriptor = {
                "source_type": "attachment",
                "path": str(path),
                "kind": "image",
                "mime_type": _IMAGE_MIME.get(path.suffix.lower(), "image/png"),
                "tool": "python",
            }
            descriptors.append(descriptor)
    return descriptors


# IPython instance
_ipython: "InteractiveShell | None" = None


registered_functions: dict[str, Callable] = {}
registered_function_tools: dict[str, str] = {}

T = TypeVar("T", bound=Callable)


def _is_literal_or_name(node: ast.AST) -> bool:
    """True when *node* cannot execute arbitrary code during evaluation."""
    return isinstance(node, ast.Constant | ast.Name)


def _single_registered_function_owner(code: str) -> str | None:
    """Return the owning tool when *code* is exactly one registered helper call.

    Arguments must be constants or names. Nested calls, attribute access, and
    other subexpressions are evaluated before the helper runs, so treating
    ``inspect_data(__import__("os").system("id"))`` as a read-only helper
    would auto-approve arbitrary Python. Fail closed on anything richer.
    """
    try:
        body = ast.parse(code, mode="exec").body
    except SyntaxError:
        return None
    if len(body) != 1 or not isinstance(body[0], ast.Expr):
        return None
    call = body[0].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return None
    if not all(_is_literal_or_name(arg) for arg in call.args):
        return None
    if not all(
        kw.arg is not None and _is_literal_or_name(kw.value) for kw in call.keywords
    ):
        return None
    return registered_function_tools.get(call.func.id)


def register_function(func: T, *, tool_name: str | None = None) -> T:
    """Register a function for IPython and optionally record its owning tool."""
    registered_functions[func.__name__] = func
    if tool_name is not None:
        registered_function_tools[func.__name__] = tool_name
    # if ipython is already initialized, push the function to it to make it available
    if _ipython is not None:
        _ipython.push({func.__name__: func})
    with _conv_ipython_lock:
        for shell in _conversation_ipython.values():
            shell.push({func.__name__: func})
    # Do not select a backend here: tool initialization runs before chat()
    # switches into the selected workspace. Bridge stubs are synced at execution.
    return func


def _detect_venv(env_only: bool = False) -> Path | None:
    """Detect the user's virtual environment, if any.

    Checks in order:
    1. VIRTUAL_ENV environment variable (set when a venv is activated)
    2. .venv directory in the session workspace (skipped if env_only=True)

    Args:
        env_only: If True, only check VIRTUAL_ENV (skip workspace detection).
                  Used during early init before the workspace is selected.
    """
    # Check VIRTUAL_ENV env var (set by `source .venv/bin/activate`)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        # Keep the explicit environment anchored to the host cwd even when a
        # kernel subprocess starts in a different session workspace.
        venv_path = Path(virtual_env).absolute()
        if venv_path.is_dir():
            return venv_path

    if not env_only:
        cwd_venv = current_workspace() / ".venv"
        if cwd_venv.is_dir():
            return cwd_venv

    return None


def _get_venv_site_packages(venv_path: Path) -> Path | None:
    """Get the site-packages directory for a venv."""
    # Windows: Lib/site-packages (no pythonX.Y subdirectory)
    win_sp = venv_path / "Lib" / "site-packages"
    if win_sp.is_dir():
        return win_sp
    # Unix: lib/pythonX.Y/site-packages
    lib_dir = venv_path / "lib"
    if not lib_dir.is_dir():
        return None
    for entry in lib_dir.iterdir():
        if entry.name.startswith("python") and entry.is_dir():
            sp = entry / "site-packages"
            if sp.is_dir():
                return sp
    return None


def _setup_venv_paths(env_only: bool = False) -> None:
    """Add the user's venv site-packages to sys.path if available.

    This allows the IPython instance (which runs in-process within gptme's
    own environment) to import packages from the user's project venv.
    See: https://github.com/gptme/gptme/issues/29

    Args:
        env_only: If True, only use VIRTUAL_ENV for detection (skip cwd check).
    """
    venv_path = _detect_venv(env_only=env_only)
    if venv_path is None:
        return

    # Don't add if this IS gptme's own venv
    if Path(sys.prefix).resolve() == venv_path.resolve():
        return

    sp = _get_venv_site_packages(venv_path)
    if sp is None:
        logger.warning("Found venv at %s but couldn't locate site-packages", venv_path)
        return

    sp_resolved = str(sp.resolve())
    if sp_resolved not in [str(Path(p).resolve()) for p in sys.path]:
        # Append after gptme's own site-packages so gptme's deps always take
        # precedence (including lazy-imported modules loaded on-demand)
        sys.path.append(sp_resolved)
        # Also process .pth files in the venv (handles editable installs etc.)
        site.addsitedir(sp_resolved)
        logger.info("Added user venv site-packages to sys.path: %s", sp_resolved)


def _new_ipython():
    """Build a fresh in-process IPython shell with the registered functions."""
    from IPython.core.displayhook import DisplayHook
    from IPython.core.interactiveshell import InteractiveShell  # fmt: skip

    class ResultDisplayHook(DisplayHook):
        """Collect results without writing them; the caller renders them once."""

        def write_output_prompt(self) -> None:
            pass

        def write_format_data(
            self, format_dict: dict, md_dict: dict | None = None
        ) -> None:
            pass

        def finish_displayhook(self) -> None:
            pass

    _setup_venv_paths()

    # Disable colors in IPython to avoid ANSI escape codes in output
    # Note: We set IPython's colors mode directly rather than using NO_COLOR
    # globally, as that would affect other parts of the application (like Rich)
    shell = InteractiveShell(displayhook_class=ResultDisplayHook)
    shell.colors = "NoColor"
    shell.push(registered_functions)
    return shell


def _get_ipython():
    """Return the in-process IPython shell for this context.

    The server serves several conversations from one process. A single shared
    ``InteractiveShell`` let a name defined in one conversation resolve in
    another (gptme/gptme#4951), so each conversation gets its own shell. CLI
    sessions have no conversation id and keep the process-global shell, which
    is what ``chat()`` and the ``gptme`` REPL expect.

    The working directory is still process-wide: ``os.chdir`` cannot be
    scoped to a shell. Server conversations pin each in-process cell to
    ``current_workspace()`` under a lock and restore the host cwd in
    ``finally`` (gptme/gptme#4951). CLI sessions keep the process cwd,
    including cell-to-cell ``os.chdir`` / ``%cd``. The lock is not full
    process isolation: threads outside it can observe the temporary cwd.
    """
    from ..hooks import current_conversation_id  # avoid an import cycle

    conversation_id = current_conversation_id.get()
    if conversation_id is None:
        global _ipython
        if _ipython is None:
            _ipython = _new_ipython()
        return _ipython

    with _conv_ipython_lock:
        shell = _conversation_ipython.get(conversation_id)
        if shell is None:
            shell = _new_ipython()
            _conversation_ipython[conversation_id] = shell
    return shell


def reset_conversation_ipython(conversation_id: str) -> None:
    """Drop a finished conversation's in-process shell.

    Keeps a long-lived server from retaining one ``InteractiveShell`` (and
    everything its namespace holds) per conversation that ever ran Python.
    """
    close_conversation_ipython(detach_conversation_ipython(conversation_id))


def detach_conversation_ipython(conversation_id: str) -> Any:
    """Detach state while the caller excludes new server sessions."""
    with _conv_ipython_lock:
        return _conversation_ipython.pop(conversation_id, None)


def close_conversation_ipython(shell: Any) -> None:
    """Close an already detached shell without touching its replacement."""
    if shell is not None:
        # Older IPython versions end the history session but do not stop its
        # saving thread or close its database. Keep the manager before shell
        # cleanup clears it, and stop the writer before closing its resources.
        history = shell.history_manager
        if history is not None and history.save_thread is not None:
            history.save_thread.stop()
        # IPython registers a bound atexit callback, which otherwise keeps the
        # shell alive even after removing it from our registry.
        atexit.unregister(shell.atexit_operations)
        try:
            shell.atexit_operations()
        finally:
            if history is not None:
                history.db.close()


def _session_end_ipython_cleanup(
    manager: "LogManager", **kwargs
) -> Generator[Message | StopPropagation, None, None]:
    """Release Python state even when the shell tool is not loaded."""
    # Server cleanup is serialized with reconnects by SessionManager; a delayed
    # SESSION_END hook must not clear a replacement session's live namespace.
    if manager.logdir and not kwargs.get("server_managed"):
        reset_conversation_ipython(manager.logdir.name)
    yield from ()


def _open_spill_file(label: str) -> "PrivateOutputFile | None":
    """Open a private ``tool-outputs/python/`` file for streaming overflow."""
    from datetime import datetime, timezone

    from ..logmanager import LogManager
    from ..util.output_storage import open_private_output_file

    manager = LogManager.get_current_log()
    if manager is None or not manager.logdir:
        return None
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return open_private_output_file(manager.logdir, "python", f"{stamp}-{label}.txt")


class TeeIO(io.StringIO):
    def __init__(self, original_stream, label: str = "output"):
        super().__init__()
        self.original_stream = original_stream
        self._label = label
        self._byte_count = 0
        self._buffered_bytes = 0
        self._truncated = False
        # Once the capture cap is hit, the overflow streams to disk (when a
        # conversation dir is active) so the dropped bytes stay recoverable
        # while memory stays bounded.
        self._spill: PrivateOutputFile | None = None
        self._spill_path: Path | None = None
        self._spill_failed = False
        # Redaction is line-based, so the spill keeps the trailing partial line
        # until its newline arrives (or `finish`) before redacting and writing.
        self._spill_pending = ""
        self._spill_seeded = False
        # Policy switch and the process's live secret values are resolved once
        # per spill instead of once per chunk.
        self._spill_redact: bool | None = None
        self._spill_literals: list[str] = []

    def _spill_append(self, s: str) -> None:
        if self._spill_failed:
            return
        if self._spill is None:
            # Open eagerly: with no conversation dir there is nowhere to retain
            # bytes, and buffering them in `_spill_pending` would defeat the
            # bounded-memory capture.
            self._spill = _open_spill_file(self._label)
            if self._spill is None:
                self._spill_failed = True
                return
        if not self._spill_seeded:
            # Everything buffered so far is the head of the full output.
            self._spill_pending = self.getvalue()
            self._spill_seeded = True
        self._spill_pending += s
        cut = self._spill_pending.rfind("\n")
        if cut == -1:
            return
        complete, self._spill_pending = (
            self._spill_pending[: cut + 1],
            self._spill_pending[cut + 1 :],
        )
        self._spill_write(complete)

    def _spill_write(self, text: str) -> None:
        """Append redacted *text* to the retained overflow file."""
        if self._spill_failed or self._spill is None:
            return
        from ..util.output_storage import _encode

        try:
            self._spill.append(_encode(self._redact_spill_text(text)))
            self._spill_path = self._spill.path
        except Exception as e:  # spilling is best-effort; capture must continue
            logger.warning("Could not spill python %s overflow: %s", self._label, e)
            self._spill_failed = True
            self._spill_path = None
            self._spill_pending = ""
            self.finish()

    def _redact_spill_text(self, text: str) -> str:
        """Redact retained bytes, resolving the policy once per spill."""
        if self._spill_redact is None:
            from ..config import get_config  # deferred: avoid import cycle

            self._spill_redact = bool(
                get_config().get_env_bool("GPTME_SHELL_REDACT_SECRETS", True)
            )
            if self._spill_redact:
                from ..util.redact import live_secret_values

                self._spill_literals = live_secret_values()
        if not self._spill_redact:
            return text
        from ..util.redact import redact_secret_values_from_output

        return redact_secret_values_from_output(
            text, live_literals=self._spill_literals
        )[0]

    def finish(self) -> None:
        """Flush the pending line and close the spill file, if any."""
        pending, self._spill_pending = self._spill_pending, ""
        if pending:
            self._spill_write(pending)
        spill, self._spill = self._spill, None
        if spill is not None:
            try:
                spill.close()
            except OSError as e:
                logger.warning("Could not close python spill file: %s", e)

    def write(self, s):

        self.original_stream.write(s)
        self.original_stream.flush()  # Ensure immediate display
        # Stop buffering once the cap is reached to avoid unbounded memory growth,
        # but keep the part of a crossing write that still fits.
        n_chars = len(s)
        encoded = s.encode("utf-8", errors="surrogatepass")
        self._byte_count += len(encoded)
        limit = _DEFAULT_MAX_OUTPUT_BYTES - _CAPTURE_MARKER_RESERVE
        room = limit - self._buffered_bytes
        if self._truncated:
            # Once truncated, the head is a closed prefix: every later write
            # goes to the spill file only, even one that would fit the slack
            # left by a dropped partial character.
            self._spill_append(s)
            return n_chars
        if len(encoded) > room:
            self._truncated = True
            self._spill_append(s)
            if room <= 0:
                return n_chars
            kept = _decode_slice(encoded[:room])
            self._buffered_bytes += len(kept.encode("utf-8", errors="surrogatepass"))
            super().write(kept)
            # Report the full write as accepted: the cap limits retention only
            return n_chars
        self._buffered_bytes += len(encoded)
        return super().write(s)

    def get_captured(self) -> str:
        """Return buffered output, appending a truncation marker if writes were dropped."""
        value = self.getvalue()
        if not self._truncated:
            return value
        self.finish()
        saved = f"; full output saved to {self._spill_path}" if self._spill_path else ""
        head = value.encode("utf-8", errors="surrogatepass")
        # The buffer never exceeds cap - marker reserve, so head + marker stays
        # within the cap and downstream _cap_output() will not truncate again.
        # A saved-path marker can outgrow the reserve: trim the head to fit.
        # Bytes trimmed from the head are omitted too, and counting them can
        # lengthen the marker, so recompute until head + marker fits.
        while True:
            dropped = self._byte_count - len(head)
            marker = (
                f"\n\n[... {dropped:,} bytes omitted "
                f"({self._byte_count:,} total — capture limit reached, head only{saved}) ...]"
            )
            excess = (
                len(head)
                + len(marker.encode("utf-8", errors="surrogatepass"))
                - _DEFAULT_MAX_OUTPUT_BYTES
            )
            if excess <= 0:
                return head.decode("utf-8", errors="surrogatepass") + marker
            head = _decode_slice(head[:-excess]).encode("utf-8", errors="surrogatepass")


@contextmanager
def capture_and_display():
    stdout_capture = TeeIO(sys.stdout, "stdout")
    stderr_capture = TeeIO(sys.stderr, "stderr")
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout_capture, stderr_capture
    try:
        yield stdout_capture, stderr_capture
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        stdout_capture.finish()
        stderr_capture.finish()


def _kernel_image_attachments(
    result: "KernelExecResult",
) -> tuple[list[Path], "list[ArtifactDescriptor]", list[str]]:
    """Write private rasters into the existing conversation attachment lifecycle."""
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    directory = manager.logdir / "attachments" if manager is not None else None
    files: list[Path] = []
    artifacts: list[ArtifactDescriptor] = []
    notices = list(result.image_notices)
    suffixes = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    for image in result.images:
        path: Path | None = None
        try:
            if directory is not None:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Without a current conversation, use the same private retained temp
            # file approach as vision. LogManager snapshots Message.files later.
            with tempfile.NamedTemporaryFile(
                prefix="kernel-",
                suffix=suffixes[image.mime_type],
                dir=directory,
                delete=False,
            ) as file:
                path = Path(file.name)
                file.write(image.data)
            files.append(path)
            if manager is not None:
                artifacts.append(
                    {
                        "source_type": "attachment",
                        "path": path.relative_to(manager.logdir).as_posix(),
                        "kind": "image",
                        "mime_type": image.mime_type,
                        "tool": "python",
                        "title": f"Kernel {image.output_type} image",
                    }
                )
        except OSError:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove incomplete kernel image %s", path)
            notices.append("Could not retain kernel image attachment; image omitted.")
    return files, artifacts, notices


def _execute_python_kernel(code: str) -> Generator[Message, None, None]:
    """Execute *code* in the separate Jupyter kernel process."""
    from .python_kernel import get_backend

    backend = get_backend()
    for name, func in registered_functions.items():
        if backend._bridge._functions.get(name) is not func:
            backend.register_bridge_function(func)
    try:
        result = backend.execute(code, timeout=_get_timeout())
    except RuntimeError as exc:
        yield Message(
            "system",
            f"Kernel error: {_redact_cell_output(str(exc))}\n\nFalling back is not automatic; set GPTME_IPYTHON_BACKEND=inprocess to use in-process mode.",
        )
        return
    except KeyboardInterrupt:
        backend.interrupt()
        backend._drain_until_idle()
        raise

    # If the cell returned Message / list[Message] objects directly (via the
    # kernel's displayhook wrapper), deliver them and skip the summary, exactly
    # as the in-process path does on isinstance(result.result, list) returns.
    if (
        backend._bridge.messages
        and result.result_text is None
        and not result.stdout
        and not result.stderr
        and not result.error
        and not result.images
        and not result.image_notices
    ):
        yield from backend._bridge.messages
        return

    yield from backend._bridge.messages
    output = ""

    if result.stdout:
        output += (
            md_codeblock("stdout", _cap_output(result.stdout, "kernel stdout").rstrip())
            + "\n\n"
        )

    if result.result_text is not None:
        result_str = _cap_output(result.result_text, "kernel result")
        output += f"Result:\n{md_codeblock('', result_str)}\n\n"

    if result.stderr:
        output += (
            md_codeblock("stderr", _cap_output(result.stderr, "kernel stderr").rstrip())
            + "\n\n"
        )

    if result.error:
        # Error text is inserted into the Message directly (not through
        # _cap_output), so it needs the redaction policy too: an exception
        # carrying a live API key would otherwise reach the provider.
        tb_str = _redact_cell_output("\n".join(result.error.get("traceback", [])))
        ename = _redact_cell_output(result.error.get("ename", "Error"))
        evalue = _redact_cell_output(result.error.get("evalue", ""))
        output += f"Exception: {ename}: {evalue}\n"
        if tb_str:
            output += md_codeblock("traceback", tb_str) + "\n\n"

    output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)

    files, artifacts, notices = _kernel_image_attachments(result)
    if notices:
        output += "\n".join(dict.fromkeys(notices)) + "\n"
    yield Message(
        "system",
        "Executed code block (kernel).\n\n" + output,
        files=list(files),
        metadata={"artifacts": artifacts} if artifacts else None,
    )


def execute_python(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Executes a python codeblock and returns the output."""

    if code is not None and args is not None:
        code = code.strip()
    elif kwargs is not None:
        code = kwargs.get("code", "").strip()

    if code is None:
        raise ValueError("Code content is required to execute Python")

    # Registered helper calls inherit their owning tool's confirmation policy when
    # the cell is exactly one function call. Arbitrary Python remains ``ipython``.
    confirmation_tool_use = None
    if owner := _single_registered_function_owner(code):
        confirmation_tool_use = ToolUse(tool=owner, args=[], content=code)
    confirm_result = get_confirmation(tool_use=confirmation_tool_use)
    if confirm_result.action != ConfirmAction.CONFIRM:
        # early return - use DECLINED_CONTENT so chat loop detects declined execution
        yield Message("system", DECLINED_CONTENT)
        return

    # Sandboxed execution path: docker or wasmtime backend.
    from ..sandbox import (
        SandboxConfig,
        sandbox_exec_python,
        sandbox_exec_wasmtime,
    )

    sandbox_cfg = SandboxConfig.from_env(workspace=current_workspace())
    if sandbox_cfg.backend in ("docker", "wasmtime"):
        ok, avail_msg = sandbox_cfg.check_available()
        if not ok:
            yield Message(
                "system",
                f"{sandbox_cfg.backend.capitalize()} sandbox unavailable: {avail_msg}\n"
                "Set GPTME_SANDBOX=none to fall back to the unsandboxed IPython REPL.",
            )
            return
        if sandbox_cfg.backend == "docker":
            stdout, stderr, returncode = sandbox_exec_python(sandbox_cfg, code)
            label = "Docker sandbox"
        else:
            stdout, stderr, returncode = sandbox_exec_wasmtime(sandbox_cfg, code)
            label = "Wasmtime sandbox"
        output = ""
        if stdout:
            output += (
                md_codeblock("stdout", _cap_output(stdout, f"{label} stdout").rstrip())
                + "\n\n"
            )
        if stderr:
            output += (
                md_codeblock("stderr", _cap_output(stderr, f"{label} stderr").rstrip())
                + "\n\n"
            )
        if returncode not in (0, None):
            output += f"Process exited with code {returncode}\n"
        yield Message(
            "system",
            f"Executed code block ({label}).\n\n" + output,
        )
        return

    # Kernel mode: execute in a separate Jupyter kernel process
    if _resolve_kernel_backend():
        # The kernel runs cells in the session workspace (see KernelBackend.execute).
        cwd = current_workspace()
        pre_images = _snapshot_images(cwd)
        msgs = list(_execute_python_kernel(code))
        post_images = _snapshot_images(cwd)
        plot_artifacts = _make_plot_artifacts(pre_images, post_images)
        if plot_artifacts and msgs:
            # Attach artifact descriptors to the last message (the execution result).
            _m = msgs[-1].metadata
            existing_meta: MessageMetadata = dict(_m) if _m else {}  # type: ignore[assignment]
            existing_meta["artifacts"] = [
                *existing_meta.get("artifacts", []),
                *plot_artifacts,
            ]
            msgs[-1] = dataclasses.replace(msgs[-1], metadata=existing_meta)
        yield from msgs
        return

    # Create an IPython instance if it doesn't exist yet
    _ipython = _get_ipython()

    # Snapshot image files where the in-process cell actually executes.
    # Server conversations pin the process cwd for this cell only (see
    # _inprocess_session_cwd); CLI keeps the process cwd.
    timeout = _get_timeout()
    with _inprocess_session_cwd():
        cwd = Path.cwd()
        pre_images = _snapshot_images(cwd)
        with (
            _cell_timeout(timeout) as timed_out,
            interruptible_sleep(),
            capture_and_display() as (stdout_capture, stderr_capture),
        ):
            # Execute the code (output will be displayed in real-time)
            result: ExecutionResult = _ipython.run_cell(
                code, silent=False, store_history=False
            )
        timed_out_fired = timed_out()
        captured_stdout = stdout_capture.get_captured()
        captured_stderr = stderr_capture.get_captured()
        post_images = pre_images if timed_out_fired else _snapshot_images(cwd)

    if timed_out_fired:
        yield Message(
            "system",
            f"Execution timed out after {timeout:g}s and was interrupted "
            "(GPTME_IPYTHON_TIMEOUT). Run long work in the background with the "
            "shell tool instead.\n\n"
            + _format_cell_output(captured_stdout, captured_stderr),
        )
        return

    output = ""
    terminal_output = ""
    # TODO: should we include captured stdout with messages like these?
    # used by vision tool and observe helpers
    if isinstance(result.result, Message):
        yield result.result
        return
    if isinstance(result.result, list):
        if result.result and all(isinstance(m, Message) for m in result.result):
            yield from result.result
            return
        # empty list or non-Message list falls through to repr output below

    if result.result is not None:
        # show stdout before result if both exist
        if captured_stdout:
            # Cap large stdout
            captured_stdout = _cap_output(captured_stdout)
            output += md_codeblock("stdout", captured_stdout.rstrip()) + "\n\n"
        result_str = str(result.result)
        # Cap large result representations
        result_str = _cap_output(result_str)
        result_output = f"Result:\n{md_codeblock('', result_str)}\n\n"
        output += result_output
        terminal_output += result_output

    elif captured_stdout:
        # Cap large stdout
        captured_stdout = _cap_output(captured_stdout)
        output += md_codeblock("stdout", captured_stdout.rstrip()) + "\n\n"
    if captured_stderr:
        # Cap large stderr (tracebacks, etc.) the same way stdout is capped
        captured_stderr = _cap_output(captured_stderr)
        output += md_codeblock("stderr", captured_stderr.rstrip()) + "\n\n"
    if result.error_in_exec:
        tb = result.error_in_exec.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        if tb:
            exception_output = (
                f"Exception during execution on line {tb.tb_lineno}:\n"
                f"  {result.error_in_exec.__class__.__name__}: "
                f"{_cap_output(str(result.error_in_exec))}"
            )
            output += exception_output
            # Do NOT add to terminal_output: the live IPython stream already
            # shows the full traceback (including the exception message) in the
            # foreground. Adding a synthesized summary here would produce an
            # extra duplicate occurrence in non-live replay contexts.

    # strip ANSI escape sequences (safety net — colors are disabled at the source
    # via NO_COLOR=1 and IPython's NoColor setting, but libraries may still emit them)
    output = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", output)

    # Detect plot files created or modified during execution and attach descriptors
    plot_artifacts = _make_plot_artifacts(pre_images, post_images)
    # stdout/stderr were already streamed through TeeIO. Keep the complete result
    # for the model and structured consumers, but render only details that were
    # not part of the live stream when LogManager prints the terminal message.
    terminal_parts = ["Executed code block."]
    if terminal_output:
        terminal_parts.append(terminal_output.rstrip())
    msg = Message(
        "system",
        "Executed code block.\n\n" + output,
        terminal_display_content="\n\n".join(terminal_parts),
    )
    if plot_artifacts:
        existing: MessageMetadata = dict(msg.metadata) if msg.metadata else {}  # type: ignore[assignment]
        existing["artifacts"] = [*existing.get("artifacts", []), *plot_artifacts]
        msg = dataclasses.replace(msg, metadata=existing)
    yield msg


@functools.lru_cache
def get_installed_python_libraries() -> list[str]:
    """Check if a select list of Python libraries are installed."""
    candidates = [
        "numpy",
        "pandas",
        "matplotlib",
        "seaborn",
        "scipy",
        "sklearn",
        "statsmodels",
        "PIL",
    ]
    installed = set()
    for candidate in candidates:
        if importlib.util.find_spec(candidate):
            installed.add(candidate)

    return sorted(installed)


def get_functions():
    return "\n".join(
        [f"- {callable_signature(fn)}" for fn in registered_functions.values()]
    )


def _instructions() -> str:
    _sandbox_backend = os.environ.get("GPTME_SANDBOX", "none").lower()
    if _sandbox_backend == "docker":
        return """
Use this tool to execute a self-contained standard Python script in a fresh Docker
container. Each call starts with no prior state. IPython syntax, registered host
functions, and host-installed libraries are unavailable; include every definition
and import needed by the script. Files in the workspace remain available.

### When to use the python tool

Use `python` for isolated computation and file-processing automation. Use `shell`
when the command must use packages or tools installed only on the host.
""".strip()
    if _sandbox_backend == "wasmtime":
        return """
Use this tool to execute a self-contained standard Python script in a Wasmtime
WASI sandbox. Each call starts with no prior state. IPython syntax, registered
host functions, and native C-extension libraries are unavailable; use pure-Python
code only. Files in the workspace are not accessible.

### When to use the python tool

Use `python` for self-contained computation using only the standard library.
For tasks that need host libraries or file access, use `shell` instead.
""".strip()
    return """
Use this tool to execute Python code in an interactive IPython session.
It responds with the execution output and final result.

### When to use the python tool

Use `python` for computation, structured data, and file-processing automation.
Prefer it over the shell for pure computation or when you need persistent
state or Python libraries.
""".strip()


instructions = _instructions()

instructions_format = {
    "markdown": """
To use it, send a codeblock using the `ipython` language tag.
If you first write the code in a normal python codeblock, remember to also execute it with the ipython codeblock.
"""
}


def examples(tool_format):
    return f"""
#### Return the last expression

> User: What is 2 + 2?
> Assistant:
{ToolUse("ipython", [], "2 + 2").to_output(tool_format)}
> System: Executed code block.
{md_codeblock("result", "4")}

#### Define and call a function

> User: Square 10
> Assistant:
{
        ToolUse(
            "ipython",
            [],
            '''
def square(n):
    return n * n
square(10)
'''.strip(),
        ).to_output(tool_format)
    }
> System: Executed code block.
{md_codeblock("result", "100")}
""".strip()


def init() -> ToolSpec:
    # Set up user's venv paths early so library detection includes user packages.
    # Only use VIRTUAL_ENV here (not workspace-based detection) because init()
    # runs before the session workspace is bound. Workspace .venv detection is
    # deferred to _get_ipython(), which runs lazily via current_workspace().
    _setup_venv_paths(env_only=True)

    # Register python functions from other tools
    for loaded_tool in get_tools():
        if loaded_tool.functions:
            for tf in loaded_tool.functions:
                register_function(tf.fn, tool_name=loaded_tool.name)

    _sandbox_backend = os.environ.get("GPTME_SANDBOX", "none").lower()
    docker_mode = _sandbox_backend == "docker"
    wasmtime_mode = _sandbox_backend == "wasmtime"
    isolated_mode = docker_mode or wasmtime_mode
    python_libraries = [] if isolated_mode else get_installed_python_libraries()
    python_libraries_str = (
        "\n".join(f"- {lib}" for lib in python_libraries)
        or "- no common libraries found"
    )

    if docker_mode:
        _appendix_full = "Docker image: " + os.environ.get(
            "GPTME_SANDBOX_DOCKER_IMAGE", "python:3.12-slim"
        )
    elif wasmtime_mode:
        _appendix_full = "Wasmtime WASI sandbox; pure-Python standard library only"
    else:
        _appendix_full = f"""Available libraries:
{python_libraries_str}

Available functions:
{get_functions()}"""

    # Concise function list for tool format (stays within OpenAI's 1024-char limit, see #1697)
    if docker_mode:
        _appendix_concise = (
            "Fresh Docker container; self-contained standard Python only"
        )
    elif wasmtime_mode:
        _appendix_concise = "Wasmtime WASI sandbox; pure-Python standard library only"
    else:
        _appendix_concise = (
            f"Available functions: {', '.join(registered_functions.keys())}"
        )

    # Merge with existing format overrides (markdown already has codeblock note).
    # NOTE: _appendix_full is placed before existing_markdown intentionally so the
    # library listing appears first. If a markdown key is ever added to instructions_format,
    # review this order — the existing content would appear *after* the function list.
    existing_markdown = tool.instructions_format.get("markdown", "").strip()
    markdown_appendix = (
        f"{_appendix_full}\n\n{existing_markdown}"
        if existing_markdown
        else _appendix_full
    )

    instructions_format_updated = {
        **tool.instructions_format,
        "markdown": markdown_appendix,
        "xml": _appendix_full,  # xml gets full library + function listing (same as markdown)
        "tool": _appendix_concise,
    }

    # create a copy with the updated instructions:
    # - instructions: short base text (shared across formats)
    # - instructions_format: format-specific appendices (full signatures for markdown/xml,
    #   concise names only for tool to stay within OpenAI's 1024-char limit)
    return dataclasses.replace(
        tool,
        instructions=_instructions(),
        instructions_format=instructions_format_updated,
    )


tool = ToolSpec(
    name="ipython",
    desc="Execute Python code",
    instructions=instructions,
    examples=examples,
    execute=execute_python,
    init=init,
    block_types=[
        # "python",
        "ipython",
        "py",
    ],
    parameters=[
        Parameter(
            name="code",
            type="string",
            description="The code to execute in the IPython shell.",
            required=True,
        ),
    ],
    load_priority=10,
    hooks={"session_end": ("session.end", _session_end_ipython_cleanup, 0)},
    hints=frozenset({"code-exec", "destructive"}),
)
__doc__ = tool.get_doc(__doc__)
