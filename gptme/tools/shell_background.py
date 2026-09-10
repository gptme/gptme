"""Background job management for shell tool.

Tracks long-running commands (dev servers, builds, etc.) in the background
with separate output capture and lifecycle management.

See Issue #576 for the original background jobs feature.
"""

import atexit
import importlib
import logging
import os
import queue
import re
import signal
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

from ..message import Message
from ..sandbox import apply_memory_limit
from ..util.context import md_codeblock

_is_windows = os.name == "nt"

try:
    select: Any = importlib.import_module("select")
except ImportError:
    select = None


logger = logging.getLogger(__name__)

# Maximum buffer size to prevent memory issues (1MB per buffer)
_MAX_BUFFER_SIZE = 1024 * 1024


def _wait_readable(fds: list[int], timeout: float | None) -> list[int]:
    """Return the subset of `fds` that are readable, waiting up to `timeout` seconds.

    POSIX-only. `_read_output` never calls this on Windows: the `_is_windows`
    branch uses non-blocking `os.read` instead. `select.poll` is not available
    on Windows, and this helper refuses to silently fall back to `select()` —
    that is the FD_SETSIZE bug this exists to close.

    Uses `poll()` rather than `select()`. `select()` is backed by `fd_set`, which
    cannot represent a descriptor >= FD_SETSIZE (1024) and raises
    `ValueError: filedescriptor out of range in select()` instead of degrading.
    Background jobs in long-lived processes and parallel test runs
    (`pytest -n 16`) routinely push descriptors past that line. `poll()` has no
    such ceiling. See gptme/gptme#3715.
    """
    if not fds:
        return []
    assert select is not None
    if not hasattr(select, "poll"):
        raise OSError(
            "select.poll is unavailable; Windows uses the non-blocking "
            "os.read path in BackgroundJob._read_output"
        )
    poller = select.poll()
    for fd in fds:
        # POLLHUP/POLLERR are reported regardless of the requested mask, so EOF
        # still wakes the poll — matching select(), which reports EOF as readable.
        poller.register(fd, select.POLLIN)
    # select() takes seconds (None == block forever); poll() takes integer
    # milliseconds (negative == block forever). Round positive sub-millisecond
    # waits up so they do not become busy-spinning non-blocking polls.
    timeout_ms = (
        -1 if timeout is None else max(0 if timeout == 0 else 1, int(timeout * 1000))
    )
    return [fd for fd, _event in poller.poll(timeout_ms)]


@dataclass
class BackgroundJob:
    """Tracks a background process with its output."""

    id: int
    command: str
    process: subprocess.Popen
    start_time: float
    stdout_buffer: list[str] = field(default_factory=list)
    stderr_buffer: list[str] = field(default_factory=list)
    _stdout_buffer_start: int = field(default=0, repr=False)
    _stderr_buffer_start: int = field(default=0, repr=False)
    _stdout_read_offset: int = field(default=0, repr=False)
    _stderr_read_offset: int = field(default=0, repr=False)
    conversation_id: str | None = None
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _buffer_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start_reader(self) -> None:
        """Start background thread to read output."""
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def _read_output(self) -> None:
        """Read stdout/stderr in background thread."""
        stdout_fd = self.process.stdout.fileno() if self.process.stdout else -1
        stderr_fd = self.process.stderr.fileno() if self.process.stderr else -1
        fds = [fd for fd in [stdout_fd, stderr_fd] if fd >= 0]

        if _is_windows:
            # Windows: use non-blocking reads with polling
            for fd in fds:
                try:
                    os.set_blocking(fd, False)
                except OSError:
                    pass
            while not self._stop_event.is_set() and self.process.poll() is None:
                for fd in fds:
                    try:
                        data = os.read(fd, 4096).decode("utf-8", errors="replace")
                        if data:
                            with self._buffer_lock:
                                if fd == stdout_fd:
                                    self._stdout_buffer_start += self._append_to_buffer(
                                        self.stdout_buffer, data
                                    )
                                else:
                                    self._stderr_buffer_start += self._append_to_buffer(
                                        self.stderr_buffer, data
                                    )
                    except BlockingIOError:
                        pass
                    except (OSError, ValueError):
                        return
                time.sleep(0.1)
        else:
            assert select is not None
            while not self._stop_event.is_set() and self.process.poll() is None:
                try:
                    readable = _wait_readable(fds, 0.1)
                    for fd in readable:
                        data = os.read(fd, 4096).decode("utf-8", errors="replace")
                        if data:
                            with self._buffer_lock:
                                if fd == stdout_fd:
                                    self._stdout_buffer_start += self._append_to_buffer(
                                        self.stdout_buffer, data
                                    )
                                else:
                                    self._stderr_buffer_start += self._append_to_buffer(
                                        self.stderr_buffer, data
                                    )
                except (OSError, ValueError):
                    break

        # Final read after process exits
        if self.process.stdout:
            try:
                remaining = self.process.stdout.read()
                if remaining:
                    with self._buffer_lock:
                        self._stdout_buffer_start += self._append_to_buffer(
                            self.stdout_buffer,
                            remaining.decode("utf-8", errors="replace"),
                        )
            except (OSError, ValueError):
                pass
        if self.process.stderr:
            try:
                remaining = self.process.stderr.read()
                if remaining:
                    with self._buffer_lock:
                        self._stderr_buffer_start += self._append_to_buffer(
                            self.stderr_buffer,
                            remaining.decode("utf-8", errors="replace"),
                        )
            except (OSError, ValueError):
                pass

        _notify_completion(self)

    def _append_to_buffer(self, buffer: list[str], data: str) -> int:
        """Append data to buffer, enforcing size limit."""
        buffer.append(data)
        # Check total size and truncate from front if needed
        total_size = sum(len(s) for s in buffer)
        removed_size = 0
        while total_size > _MAX_BUFFER_SIZE and len(buffer) > 1:
            removed = buffer.pop(0)
            total_size -= len(removed)
            removed_size += len(removed)
        return removed_size

    def get_output(self, *, incremental: bool = False) -> tuple[str, str]:
        """Get accumulated stdout and stderr, optionally since the last read."""
        with self._buffer_lock:
            stdout = "".join(self.stdout_buffer)
            stderr = "".join(self.stderr_buffer)
            if not incremental:
                return stdout, stderr

            stdout_start = max(self._stdout_read_offset - self._stdout_buffer_start, 0)
            stderr_start = max(self._stderr_read_offset - self._stderr_buffer_start, 0)
            new_stdout = stdout[stdout_start:]
            new_stderr = stderr[stderr_start:]
            self._stdout_read_offset = self._stdout_buffer_start + len(stdout)
            self._stderr_read_offset = self._stderr_buffer_start + len(stderr)
            return new_stdout, new_stderr

    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None

    def is_output_complete(self) -> bool:
        """Check whether the reader has drained all inherited output pipes."""
        return self._reader_thread is None or not self._reader_thread.is_alive()

    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def kill(self) -> None:
        """Terminate the background job and its process group."""
        self._stop_event.set()
        if self.process.poll() is not None:
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=1.0)
            return
        try:
            if _is_windows:
                self.process.terminate()
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            if _is_windows:
                self.process.kill()
            else:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            self.process.wait()
        except ProcessLookupError:
            pass
        # Join reader thread to ensure clean shutdown
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)


# Jobs are scoped to the active conversation. A ``None`` key covers direct
# library/tests calls that have no conversation context.
_background_jobs: dict[str | None, dict[int, BackgroundJob]] = {}
_next_job_ids: dict[str | None, int] = {}
_completion_queue: queue.Queue[BackgroundJob] = queue.Queue()
_job_lock: threading.Lock = threading.Lock()


def _current_conversation_id() -> str | None:
    from ..hooks import current_conversation_id
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    return current_conversation_id.get() or (manager.chat_id if manager else None)


def _get_next_job_id_locked(conversation_id: str | None) -> int:
    """Get the next conversation-local job ID while holding ``_job_lock``."""
    job_id = _next_job_ids.get(conversation_id, 1)
    _next_job_ids[conversation_id] = job_id + 1
    return job_id


def _notify_completion(job: BackgroundJob) -> None:
    _completion_queue.put(job)


def _jobs_for(conversation_id: str | None) -> dict[int, BackgroundJob]:
    return _background_jobs.setdefault(conversation_id, {})


def _get_background_job(
    conversation_id: str | None, job_id: int
) -> BackgroundJob | None:
    with _job_lock:
        return _background_jobs.get(conversation_id, {}).get(job_id)


def start_background_job(
    command: str, memory_limit: int | None = None
) -> BackgroundJob:
    """Start a command as a background job (thread-safe)."""
    conversation_id = _current_conversation_id()

    # Start process with separate stdout/stderr pipes
    popen_kwargs: dict = {}
    shell_cmd: list[str] = ["bash", "-c", command]
    if not _is_windows:
        popen_kwargs["start_new_session"] = True
        if memory_limit is not None:
            shell_cmd = apply_memory_limit(shell_cmd, memory_limit)
    process = subprocess.Popen(
        shell_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        **popen_kwargs,
    )

    with _job_lock:
        job_id = _get_next_job_id_locked(conversation_id)
        job = BackgroundJob(
            id=job_id,
            command=command,
            process=process,
            start_time=time.time(),
            conversation_id=conversation_id,
        )
        _jobs_for(conversation_id)[job_id] = job
    job.start_reader()
    return job


def get_background_job(job_id: int) -> BackgroundJob | None:
    """Get a background job in the active conversation."""
    with _job_lock:
        return _jobs_for(_current_conversation_id()).get(job_id)


def list_background_jobs() -> list[BackgroundJob]:
    """List jobs in the active conversation, including completed jobs."""
    with _job_lock:
        return list(_jobs_for(_current_conversation_id()).values())


def cleanup_finished_jobs() -> None:
    """Compatibility no-op: completed jobs remain available until session end."""
    return


def _purge_completion_queue(conversation_ids: set[str | None]) -> None:
    """Remove queued completions for conversations whose jobs were reset."""
    retained: list[BackgroundJob] = []
    while True:
        try:
            job = _completion_queue.get_nowait()
        except queue.Empty:
            break
        if job.conversation_id not in conversation_ids:
            retained.append(job)
    for job in retained:
        _completion_queue.put(job)


def reset_background_jobs(
    conversation_id: str | None = None, *, all_conversations: bool = True
) -> None:
    """Stop and remove jobs globally, or only for ``conversation_id``."""
    with _job_lock:
        if all_conversations:
            conversation_ids = set(_background_jobs) | set(_next_job_ids)
            groups = list(_background_jobs.values())
            _background_jobs.clear()
            _next_job_ids.clear()
        else:
            conversation_ids = {conversation_id}
            groups = [_background_jobs.pop(conversation_id, {})]
            _next_job_ids.pop(conversation_id, None)
        _purge_completion_queue(conversation_ids)
    for jobs in groups:
        for job in jobs.values():
            if job.is_running():
                job.kill()


# Register cleanup handler to prevent orphaned bg jobs when gptme exits (Issue #993)
atexit.register(reset_background_jobs)


def _completion_message(job: BackgroundJob) -> Message:
    status = f"exit code {job.process.returncode}"
    stdout, stderr = job.get_output()
    details: list[str] = []
    if stdout:
        details.append(md_codeblock("stdout", stdout[-8000:]))
    if stderr:
        details.append(md_codeblock("stderr", stderr[-2000:]))
    suffix = "\n\n" + "\n\n".join(details) if details else ""
    return Message(
        "system",
        f"Background shell job #{job.id} finished ({status}): `{job.command}`{suffix}",
    )


def background_job_completion_hook(
    manager: object,
    interactive: bool,
    prompt_queue: object,
    no_confirm: bool = False,
) -> Generator[Message, None, None]:
    """Deliver completed jobs only to the conversation that started them."""
    del interactive, prompt_queue, no_confirm
    conversation_id = getattr(manager, "chat_id", None)
    deferred: list[BackgroundJob] = []
    while True:
        try:
            job = _completion_queue.get_nowait()
        except queue.Empty:
            break
        if job.conversation_id != conversation_id:
            deferred.append(job)
            continue
        # Match object identity as well as the conversation-local ID. A reset can
        # reuse IDs; an old queued completion must never resolve to the new job.
        if _get_background_job(conversation_id, job.id) is job:
            yield _completion_message(job)
    for job in deferred:
        _completion_queue.put(job)


# Background command handlers


def execute_bg_command(
    command: str, memory_limit: int | None = None
) -> Generator[Message, None, None]:
    """Start a command as a background job."""
    from .shell_validation import is_denylisted

    if not command.strip():
        yield Message("system", "Usage: `bg <command>`\n\nExample: `bg npm run dev`")
        return

    # Check if command is denylisted - blocked even for background jobs
    is_denied, deny_reason, matched_cmd = is_denylisted(command)
    if is_denied:
        yield Message(
            "system", f"Background command denied: `{matched_cmd}`\n\n{deny_reason}"
        )
        return

    job = start_background_job(command, memory_limit=memory_limit)
    yield Message(
        "system",
        f"Started background job **#{job.id}**: `{command}`\n\n"
        f"Use these commands to manage it:\n"
        f"- `jobs` - List all background jobs\n"
        f"- `output {job.id}` - Show output from job #{job.id}\n"
        f"- `wait {job.id}` - Wait for job #{job.id} to finish\n"
        f"- `kill {job.id}` - Terminate job #{job.id}",
    )


def execute_jobs_command() -> Generator[Message, None, None]:
    """List all background jobs."""
    jobs = list_background_jobs()
    if not jobs:
        yield Message("system", "No background jobs running.")
        return

    lines = ["**Background Jobs:**\n"]
    for job in jobs:
        status = "🟢 Running" if job.is_running() else "⚫ Finished"
        elapsed = job.elapsed_time()
        if elapsed < 60:
            time_str = f"{elapsed:.1f}s"
        else:
            time_str = f"{elapsed / 60:.1f}m"
        lines.append(
            f"- **#{job.id}** [{status}] ({time_str}): `{job.command[:50]}{'...' if len(job.command) > 50 else ''}`"
        )

    yield Message("system", "\n".join(lines))


def execute_output_command(job_id_str: str) -> Generator[Message, None, None]:
    """Show output from a background job."""
    parts = job_id_str.split()
    incremental = False
    if len(parts) == 2 and parts[1] == "--new":
        incremental = True
    elif len(parts) != 1:
        yield Message("system", "Usage: `output <job-id> [--new]`")
        return

    try:
        job_id = int(parts[0])
    except ValueError:
        yield Message(
            "system", f"Invalid job ID: `{job_id_str}`. Use `jobs` to list active jobs."
        )
        return

    job = get_background_job(job_id)
    if not job:
        yield Message(
            "system", f"No job with ID #{job_id}. Use `jobs` to list active jobs."
        )
        return

    stdout, stderr = job.get_output(incremental=incremental)
    if job.is_running():
        status = "Running"
    elif not job.is_output_complete():
        status = (
            f"Finished (exit code: {job.process.returncode}; output still draining)"
        )
    else:
        status = f"Finished (exit code: {job.process.returncode})"
    elapsed = job.elapsed_time()

    msg = f"**Job #{job_id}** - {status} ({elapsed:.1f}s)\n"
    msg += f"Command: `{job.command}`\n\n"
    if not job.is_running() and not job.is_output_complete():
        msg += (
            "The command exited, but a descendant still holds an output pipe. "
            f"Use `wait {job_id}` again or `output {job_id} --new` to collect "
            "the remaining output.\n\n"
        )

    if stdout:
        # Truncate if too long
        if len(stdout) > 8000:
            stdout = stdout[-8000:]
            msg += md_codeblock("stdout", "...(truncated)...\n" + stdout) + "\n\n"
        else:
            msg += md_codeblock("stdout", stdout) + "\n\n"
    if stderr:
        if len(stderr) > 2000:
            stderr = stderr[-2000:]
            msg += md_codeblock("stderr", "...(truncated)...\n" + stderr) + "\n\n"
        else:
            msg += md_codeblock("stderr", stderr) + "\n\n"
    if not stdout and not stderr:
        msg += "No new output.\n" if incremental else "No output yet.\n"

    yield Message("system", msg)


def execute_wait_command(
    job_id_str: str, timeout_str: str | None = None
) -> Generator[Message, None, None]:
    """Wait for a background job to finish, up to an optional timeout."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        yield Message(
            "system", f"Invalid job ID: `{job_id_str}`. Use `jobs` to list active jobs."
        )
        return

    timeout: float | None = None
    if timeout_str is not None:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", timeout_str.lower())
        if not match:
            yield Message(
                "system",
                "Invalid timeout. Use seconds or a suffix such as `30s`, `2m`, or `1h`.",
            )
            return
        value = float(match.group(1))
        multiplier = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
        timeout = value * multiplier

    job = get_background_job(job_id)
    if not job:
        yield Message(
            "system", f"No job with ID #{job_id}. Use `jobs` to list active jobs."
        )
        return

    try:
        job.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        yield Message(
            "system",
            f"Job #{job_id} is still running after {timeout_str}. "
            f"Use `wait {job_id}` to keep waiting or `output {job_id} --new` to poll output.",
        )
        return

    if job._reader_thread and job._reader_thread.is_alive():
        job._reader_thread.join(timeout=1.0)
    yield from execute_output_command(str(job_id))


def execute_kill_command(job_id_str: str) -> Generator[Message, None, None]:
    """Terminate a background job."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        yield Message(
            "system", f"Invalid job ID: `{job_id_str}`. Use `jobs` to list active jobs."
        )
        return

    job = get_background_job(job_id)
    if not job:
        yield Message(
            "system", f"No job with ID #{job_id}. Use `jobs` to list active jobs."
        )
        return

    if not job.is_running():
        yield Message(
            "system",
            f"Job #{job_id} is already finished (exit code: {job.process.returncode}).",
        )
        return

    job.kill()
    yield Message("system", f"Terminated job #{job_id}: `{job.command}`")
