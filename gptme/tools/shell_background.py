"""Background job management for shell tool.

Tracks long-running commands (dev servers, builds, etc.) in the background
with separate output capture and lifecycle management.

See Issue #576 for the original background jobs feature.
"""

import atexit
import importlib
import logging
import os
import re
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
                    readable, _, _ = select.select(fds, [], [], 0.1)
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
        """Terminate the background job."""
        self._stop_event.set()
        try:
            self.process.terminate()
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        # Join reader thread to ensure clean shutdown
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)


# Global storage for background jobs
_background_jobs: dict[int, BackgroundJob] = {}
_next_job_id: int = 1
_job_lock: threading.Lock = threading.Lock()


def _get_next_job_id() -> int:
    """Get next available job ID (thread-safe)."""
    global _next_job_id
    with _job_lock:
        job_id = _next_job_id
        _next_job_id += 1
        return job_id


def start_background_job(
    command: str, memory_limit: int | None = None
) -> BackgroundJob:
    """Start a command as a background job (thread-safe)."""
    # Proactively clean up finished jobs to prevent memory accumulation
    cleanup_finished_jobs()

    job_id = _get_next_job_id()

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

    job = BackgroundJob(
        id=job_id,
        command=command,
        process=process,
        start_time=time.time(),
    )
    job.start_reader()

    with _job_lock:
        _background_jobs[job_id] = job

    return job


def get_background_job(job_id: int) -> BackgroundJob | None:
    """Get a background job by ID."""
    with _job_lock:
        return _background_jobs.get(job_id)


def list_background_jobs() -> list[BackgroundJob]:
    """List all background jobs, cleaning up finished ones first."""
    cleanup_finished_jobs()
    with _job_lock:
        return list(_background_jobs.values())


def cleanup_finished_jobs() -> None:
    """Remove finished jobs from tracking (thread-safe)."""
    with _job_lock:
        finished = [
            job_id
            for job_id, job in _background_jobs.items()
            if not job.is_running() and job.is_output_complete()
        ]
        for job_id in finished:
            del _background_jobs[job_id]


def reset_background_jobs() -> None:
    """Stop and clean up all background jobs. Called on exit and for testing."""
    global _next_job_id
    with _job_lock:
        # Kill any running jobs
        for job in _background_jobs.values():
            if job.is_running():
                job.kill()
        _background_jobs.clear()
        _next_job_id = 1


# Register cleanup handler to prevent orphaned bg jobs when gptme exits (Issue #993)
atexit.register(reset_background_jobs)


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
