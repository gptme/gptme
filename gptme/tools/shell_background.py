"""Background job management for shell tool.

Tracks long-running commands (dev servers, builds, etc.) in the background
with separate output capture and lifecycle management.

See Issue #576 for the original background jobs feature.
"""

import atexit
import hashlib
import importlib
import logging
import math
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from ..hooks.types import StopPropagation
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

# Control-file fingerprints already yielded as a wait interruption. The file is
# left for STEP_PRE's subagent cancel checkpoint; yielding again on the same
# contents would inject a dummy system message every LOOP_CONTINUE and burn
# model calls until timeout (parent sessions never consume control.jsonl).
# Hash contents, not mtime/size: a same-size rewrite (new cancel op) must
# interrupt wait again even on filesystems with coarse timestamps.
_control_wait_keys: set[tuple[str, bytes]] = set()


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
    process_group_id: int | None = field(default=None, repr=False)
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _buffer_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _completion_notified: bool = field(default=False, repr=False)
    _wait_expired: bool = field(default=False, repr=False)

    def start_reader(self) -> None:
        """Start background thread to read output."""
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def _append_output(self, fd: int, stdout_fd: int, data: bytes) -> None:
        """Decode and append one output chunk while maintaining offsets."""
        text = data.decode("utf-8", errors="replace")
        with self._buffer_lock:
            if fd == stdout_fd:
                self._stdout_buffer_start += self._append_to_buffer(
                    self.stdout_buffer, text
                )
            else:
                self._stderr_buffer_start += self._append_to_buffer(
                    self.stderr_buffer, text
                )

    def _read_output(self) -> None:
        """Read stdout/stderr until the child exits, then drain briefly."""
        stdout_fd = self.process.stdout.fileno() if self.process.stdout else -1
        stderr_fd = self.process.stderr.fileno() if self.process.stderr else -1
        open_fds = {fd for fd in (stdout_fd, stderr_fd) if fd >= 0}

        if _is_windows:
            # Windows: use non-blocking reads with polling.
            for fd in open_fds:
                try:
                    os.set_blocking(fd, False)
                except OSError:
                    pass

        exited_at: float | None = None
        while not self._stop_event.is_set():
            if self.process.poll() is None:
                exited_at = None
            elif not open_fds:
                break
            elif exited_at is None:
                exited_at = time.monotonic()
            elif time.monotonic() - exited_at >= 1.0:
                # A descendant may inherit the pipes after the direct child exits.
                # Bound the drain so that completion notification cannot stall on it.
                break

            if not open_fds:
                # Closing both streams does not itself mean that the process exited.
                time.sleep(0.1)
                continue

            try:
                readable = (
                    list(open_fds)
                    if _is_windows
                    else _wait_readable(list(open_fds), 0.1)
                )
                read_any = False
                for fd in readable:
                    try:
                        raw = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    except (OSError, ValueError):
                        open_fds.discard(fd)
                        continue
                    if not raw:
                        open_fds.discard(fd)
                        continue
                    read_any = True
                    self._append_output(fd, stdout_fd, raw)
                if _is_windows and not read_any:
                    time.sleep(0.1)
            except (OSError, ValueError):
                break

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
        if self.process.poll() is None:
            try:
                if _is_windows:
                    self.process.terminate()
                else:
                    assert self.process_group_id is not None
                    os.killpg(self.process_group_id, signal.SIGTERM)
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                if _is_windows:
                    self.process.kill()
                else:
                    assert self.process_group_id is not None
                    os.killpg(self.process_group_id, signal.SIGKILL)
                self.process.wait()
            except ProcessLookupError:
                pass
        # Let the reader drain regular completed jobs. Force inherited pipes to
        # stop only when their post-exit grace has elapsed.
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.5)
            if self._reader_thread.is_alive():
                self._stop_event.set()
                self._reader_thread.join(timeout=1.0)
        if self._reader_thread and self._reader_thread.is_alive():
            logger.warning("Background output reader did not stop for job #%s", self.id)


# Jobs are scoped to the active conversation. A ``None`` key covers direct
# library/tests calls that have no conversation context.
_background_jobs: dict[str | None, dict[int, BackgroundJob]] = {}
_next_job_ids: dict[str | None, int] = {}
_completion_queue: queue.Queue[BackgroundJob] = queue.Queue()
_job_lock: threading.RLock = threading.RLock()
_job_conditions: dict[str | None, threading.Condition] = {}


def _current_conversation_id() -> str | None:
    from ..hooks import current_conversation_id
    from ..logmanager import LogManager

    # Server tool calls bind the owning conversation explicitly. CLI calls use
    # the context-local LogManager established by chat().
    manager = LogManager.get_current_log()
    return current_conversation_id.get() or (manager.chat_id if manager else None)


def _get_next_job_id_locked(conversation_id: str | None) -> int:
    """Get the next conversation-local job ID while holding ``_job_lock``."""
    job_id = _next_job_ids.get(conversation_id, 1)
    _next_job_ids[conversation_id] = job_id + 1
    return job_id


def _pending_wait_jobs(conversation_id: str | None) -> list[BackgroundJob]:
    """Jobs this conversation should still wait for. Caller holds ``_job_lock``."""
    return [
        job
        for job in _background_jobs.get(conversation_id, {}).values()
        if not job._completion_notified and not job._wait_expired
    ]


def _notify_completion(job: BackgroundJob) -> None:
    with _job_lock:
        # A reader can finish after session cleanup. Never resurrect its event,
        # or deliver it to a later session that reused the same job ID.
        if _background_jobs.get(job.conversation_id, {}).get(job.id) is not job:
            return
        job._completion_notified = True
        _completion_queue.put(job)
        if condition := _job_conditions.get(job.conversation_id):
            condition.notify_all()


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
            # ``start_new_session`` makes the child PID its process-group ID.
            # Capture it at creation rather than resolving the PID during kill,
            # after the leader may have exited and its PID may have been reused.
            process_group_id=None if _is_windows else process.pid,
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


def _purge_completion_queue(conversation_ids: set[str | None] | None) -> None:
    """Remove queued completions for conversations whose jobs were reset.

    Pass ``None`` to clear all pending completions unconditionally (used when
    resetting all conversations so that entries from conversations that were
    never registered in ``_background_jobs`` don't linger across tests/resets).
    """
    with _completion_queue.mutex:
        if conversation_ids is None:
            _completion_queue.queue.clear()
        else:
            retained = type(_completion_queue.queue)(
                job
                for job in _completion_queue.queue
                if job.conversation_id not in conversation_ids
            )
            _completion_queue.queue = retained


def reset_background_jobs(
    conversation_id: str | None = None, *, all_conversations: bool = True
) -> None:
    """Stop and remove jobs globally, or only for ``conversation_id``."""
    with _job_lock:
        if all_conversations:
            groups = list(_background_jobs.values())
            _background_jobs.clear()
            _next_job_ids.clear()
            # Clear all pending completions: filtering by currently-tracked
            # conversation IDs would leave entries from jobs created outside
            # start_background_job (e.g. via _make_job in tests).
            _purge_completion_queue(None)
            conditions = list(_job_conditions.values())
            _job_conditions.clear()
            _control_wait_keys.clear()
        else:
            conversation_ids = {conversation_id}
            groups = [_background_jobs.pop(conversation_id, {})]
            _next_job_ids.pop(conversation_id, None)
            _purge_completion_queue(conversation_ids)
            condition = _job_conditions.pop(conversation_id, None)
            conditions = [condition] if condition is not None else []
        for condition in conditions:
            condition.notify_all()
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
    interactive: bool = False,
    prompt_queue: object = None,
    no_confirm: bool = False,
) -> Generator[Message, None, None]:
    """Deliver completed jobs only to the conversation that started them."""
    del interactive, prompt_queue, no_confirm
    from ..hooks import current_conversation_id

    # The hook's manager identifies the conversation being advanced. Some server
    # paths also bind an explicit context for tools; use that only when the
    # manager cannot provide an ID. Never use process-local LogManager state here.
    conversation_id = getattr(manager, "chat_id", None) or current_conversation_id.get()
    # Reset takes these locks in the same order. Holding both while claiming and
    # validating completions makes delivery atomic with conversation teardown.
    with _job_lock, _completion_queue.mutex:
        own_jobs = cast(
            list[BackgroundJob],
            [
                job
                for job in _completion_queue.queue
                if job.conversation_id == conversation_id
                and _background_jobs.get(conversation_id, {}).get(job.id) is job
            ],
        )
        own_job_ids = {id(job) for job in own_jobs}
        _completion_queue.queue = type(_completion_queue.queue)(
            job for job in _completion_queue.queue if id(job) not in own_job_ids
        )
        messages = [_completion_message(job) for job in own_jobs]
    yield from messages


def _background_wait_input(
    manager: object,
) -> list[Message] | Literal["hook"] | None:
    """Yield to existing file-based input/control, including subagent budgets."""
    from ..constants import MAX_PROMPT_QUEUE_SIZE
    from ..prompt_queue import drain_prompt_queue, drain_steer_prompts

    logdir = getattr(manager, "logdir", None)
    if logdir is None:
        return None
    # Subagents are optional: do not load that tool just to wait for a shell.
    if "gptme.tools.subagent.types" in sys.modules:
        from .complete import SessionCompleteException
        from .subagent.types import (
            ReturnType,
            _subagents,
            _subagents_lock,
            set_subagent_result_if_absent,
        )
        from .subagent.types import (
            _completion_queue as subagent_completions,
        )
        from .subagent.types import (
            _progress_queue as subagent_progress,
        )

        with _subagents_lock:
            child = next((s for s in _subagents if s.logdir == logdir), None)
        if child is not None:
            if child.cancel_event.is_set():
                set_subagent_result_if_absent(
                    child.agent_id,
                    ReturnType("cancelled", "Cancelled during background wait"),
                )
                raise SessionCompleteException(
                    "Subagent cancelled during background wait"
                )
            if (
                child.max_time is not None
                and time.time() >= child.started_at + child.max_time
            ):
                # Win the same first-writer race as the watchdog; otherwise
                # normal chat shutdown could cache this timeout as success.
                set_subagent_result_if_absent(
                    child.agent_id,
                    ReturnType("timeout", "max_time reached during background wait"),
                )
                raise SessionCompleteException(
                    "Subagent max_time reached during background wait"
                )
        # The existing subagent LOOP_CONTINUE hook still owns these queues.
        # Let it run instead of hiding its events behind a long shell job.
        if not subagent_completions.empty() or not subagent_progress.empty():
            return "hook"
    # Use the existing locked readers, not file size: an idle CLI has no next
    # STEP_PRE to consume steering unless we actually queue that input here.
    # Invalid/partial records remain on disk and cannot cause a spurious exit.
    messages = drain_prompt_queue(logdir, max_items=MAX_PROMPT_QUEUE_SIZE)
    messages += drain_steer_prompts(
        logdir, max_items=MAX_PROMPT_QUEUE_SIZE - len(messages)
    )
    if messages:
        return messages
    control = logdir / "control.jsonl"
    try:
        payload = control.read_bytes()
    except FileNotFoundError:
        return None
    if not payload:
        return None
    # Leave the file for STEP_PRE. Yield once so a subagent cancel can re-enter
    # that checkpoint; a second yield of the same contents is an infinite loop
    # because the parent cancel hook no-ops when agent_id is unset.
    key = (str(control.resolve()), hashlib.sha256(payload).digest())
    if key in _control_wait_keys:
        return None
    _control_wait_keys.add(key)
    return [Message("system", "Background wait yielded to pending session input.")]


def background_job_wait_hook(
    manager: object,
    interactive: bool,
    prompt_queue: object,
    no_confirm: bool = False,
) -> Generator[Message | StopPropagation, None, None]:
    """Wake an idle CLI on completion, before auto-reply/stuck detection.

    Job completion is condition-driven. The one-second control checkpoint is
    only for cross-process prompt/cancel files and subagent wall-clock limits;
    it does not poll processes or make model calls.
    """
    from ..config import get_config
    from ..hooks import current_conversation_id
    from ..util.interrupt import clear_interruptible, set_interruptible

    del no_confirm
    conversation_id = getattr(manager, "chat_id", None) or current_conversation_id.get()
    messages = list(background_job_completion_hook(manager))
    if messages:
        yield from messages
        yield StopPropagation()
        return
    if interactive or prompt_queue:
        return

    raw_limit = get_config().get_env("WATCH_IDLE_MAX", "1800") or "1800"
    try:
        limit = float(raw_limit)
        if not math.isfinite(limit) or limit < 0:
            raise ValueError
    except ValueError:
        logger.warning("Invalid GPTME_WATCH_IDLE_MAX %r; using 1800 seconds", raw_limit)
        limit = 1800.0
    deadline = time.monotonic() + limit
    set_interruptible()
    try:
        while True:
            with _job_lock:
                messages = list(background_job_completion_hook(manager))
                pending = _pending_wait_jobs(conversation_id)
                if messages or not pending:
                    break
            if incoming := _background_wait_input(manager):
                if isinstance(incoming, list):
                    messages = incoming
                break
            with _job_lock:
                # Recheck under the notifier's lock so a completion between the
                # last drain and wait() cannot be lost. Recompute pending from
                # live state: a job whose completion was already claimed (STEP_PRE
                # or a concurrent drain) must not abort wait for remaining jobs.
                messages = list(background_job_completion_hook(manager))
                pending = _pending_wait_jobs(conversation_id)
                if messages or not pending:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    for job in pending:
                        job._wait_expired = True
                    messages = [
                        Message(
                            "system",
                            f"Background wait timed out after {limit:g}s; {len(pending)} job(s) still pending. "
                            "Automatic waiting is disabled for these jobs; late completions will still be reported. "
                            "Use `output <id>`, `wait <id> [timeout]`, or `kill <id>` as needed.",
                        )
                    ]
                    break
                condition = _job_conditions.setdefault(
                    conversation_id, threading.Condition(_job_lock)
                )
                condition.wait(timeout=min(remaining, 1.0))
    finally:
        clear_interruptible()
    if messages:
        yield from messages
        yield StopPropagation()


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
