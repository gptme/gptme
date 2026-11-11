"""Background job management for shell tool.

Tracks long-running commands (dev servers, builds, etc.) in the background
with separate output capture and lifecycle management.

See Issue #576 for the original background jobs feature.
"""

import atexit
import codecs
import hashlib
import importlib
import json
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
from collections.abc import Callable, Generator
from contextvars import copy_context
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from ..hooks._policy_block import policy_block_message
from ..hooks.types import StopPropagation
from ..message import Message
from ..sandbox import apply_memory_limit
from ..util.context import md_codeblock
from ..util.output_storage import (
    TOOL_OUTPUTS_DIRNAME,
    PrivateOutputFile,
    UnsafeOutputPathError,
    _write_private_output,
    open_private_output_file,
)

_is_windows = os.name == "nt"

try:
    select: Any = importlib.import_module("select")
except ImportError:
    select = None


logger = logging.getLogger(__name__)

# Maximum buffer size to prevent memory issues (1MB per buffer)
_MAX_BUFFER_SIZE = 1024 * 1024

# Incomplete last line / partial assignment held before spool commit.
_SPOOL_PENDING_MAX = 64 * 1024

# Slice-2 spool gates (docs/design/file-backed-output.md).
# The pipe reader never blocks on disk: it enqueues sanitized bytes onto a
# bounded writer queue. Saturation or quota exhaustion stops persistence,
# marks capture partial, and keeps draining the child.
# Bound queued persistence, not the pipe reader. 1024 * ~4 KiB reads holds the
# 2.3 MiB evidence job even if the writer has not been scheduled yet.
_SPOOL_QUEUE_MAX = 1024
_SPOOL_QUEUE_MAX_BYTES = 4 * 1024 * 1024
_SPOOL_QUEUE_PUT_TIMEOUT = 0.05
_SPOOL_STREAM_QUOTA = 32 * 1024 * 1024
_SPOOL_CONVERSATION_QUOTA = 128 * 1024 * 1024
_SPOOL_WRITER_JOIN_TIMEOUT = 2.0
_SPOOL_QUOTA_MARKER = b"[omitted: retained-stream quota exceeded]\n"
_conversation_spool_bytes: dict[str | None, int] = {}
_conversation_spool_seeded: set[str | None] = set()
_conversation_spool_lock = threading.Lock()

# Tail of each stream shown in `output N`, promotion and completion messages.
# Longer output is saved to a per-job log file (when the conversation has a
# logdir) and the message names that file.
_STDOUT_DISPLAY_CHARS = 8000
_STDERR_DISPLAY_CHARS = 2000

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


def _seed_conversation_spool(conversation_id: str | None, log_dir: Path) -> None:
    """Count conversation-local spool files once per process restart."""
    jobs_dir = log_dir / "tool-outputs" / "shell-jobs"
    total = 0
    try:
        entries = list(jobs_dir.iterdir()) if jobs_dir.is_dir() else []
    except OSError:
        entries = []
    for path in entries:
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    with _conversation_spool_lock:
        if conversation_id in _conversation_spool_seeded:
            return
        used = _conversation_spool_bytes.get(conversation_id, 0)
        if total > used:
            _conversation_spool_bytes[conversation_id] = total
        _conversation_spool_seeded.add(conversation_id)


def _try_reserve_conversation_spool(conversation_id: str | None, n: int) -> int:
    """Reserve up to ``n`` retained bytes against the conversation allowance."""
    if n <= 0:
        return 0
    with _conversation_spool_lock:
        used = _conversation_spool_bytes.get(conversation_id, 0)
        remaining = max(0, _SPOOL_CONVERSATION_QUOTA - used)
        granted = min(n, remaining)
        if granted:
            _conversation_spool_bytes[conversation_id] = used + granted
        return granted


def _conversation_spool_remaining(conversation_id: str | None) -> int:
    with _conversation_spool_lock:
        used = _conversation_spool_bytes.get(conversation_id, 0)
    return max(0, _SPOOL_CONVERSATION_QUOTA - used)


def _release_conversation_spool(conversation_id: str | None, n: int) -> None:
    if n <= 0:
        return
    with _conversation_spool_lock:
        used = _conversation_spool_bytes.get(conversation_id, 0)
        _conversation_spool_bytes[conversation_id] = max(0, used - n)


@dataclass
class BackgroundJob:
    """Tracks a background process with its output."""

    id: int
    command: str
    process: Any
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
    _capture_finished: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _buffer_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _completion_notified: bool = field(default=False, repr=False)
    _wait_expired: bool = field(default=False, repr=False)
    _stdout_decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"),
        repr=False,
    )
    _stderr_decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")("replace"),
        repr=False,
    )
    _kill_callback: Callable[[], None] | None = field(default=None, repr=False)
    _close_callback: Callable[[], None] | None = field(default=None, repr=False)
    _output_callback: Callable[[], tuple[str, str]] | None = field(
        default=None, repr=False
    )
    # Conversation logdir captured at registration; full output of a job whose
    # displayed output is truncated is written under it.
    log_dir: Path | None = field(default=None, repr=False)
    log_path: Path | None = field(default=None, repr=False)
    stderr_log_path: Path | None = field(default=None, repr=False)
    # Set once the log holds the job's final, complete output (not a snapshot).
    _final_log_written: bool = field(default=False, repr=False)
    # A closed fallback snapshot may still lack an evicted RAM prefix.
    _snapshot_log_finished: bool = field(default=False, repr=False)
    _stdout_spool: PrivateOutputFile | None = field(default=None, repr=False)
    _stderr_spool: PrivateOutputFile | None = field(default=None, repr=False)
    _snapshot_stream: PrivateOutputFile | None = field(default=None, repr=False)
    _spool_pending_stdout: str = field(default="", repr=False)
    _spool_pending_stderr: str = field(default="", repr=False)
    _spool_discard_stdout: bool = field(default=False, repr=False)
    _spool_discard_stderr: bool = field(default=False, repr=False)
    _spool_from_reader: bool = field(default=False, repr=False)
    _capture_partial: bool = field(default=False, repr=False)
    _partial_reasons: set[str] = field(default_factory=set, repr=False)
    _spool_queue: queue.Queue[tuple[PrivateOutputFile, bool, bytes] | None] | None = (
        field(default=None, repr=False)
    )
    _spool_queued_bytes: int = field(default=0, repr=False)
    _spool_queue_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False
    )
    _spool_writer_stop: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _spool_quota_refused: bool = field(default=False, repr=False)
    _spool_writer_thread: threading.Thread | None = field(default=None, repr=False)
    _spool_persist_stdout: bool = field(default=True, repr=False)
    _spool_persist_stderr: bool = field(default=True, repr=False)
    _stdout_accepted_bytes: int = field(default=0, repr=False)
    _stderr_accepted_bytes: int = field(default=0, repr=False)
    _stdout_committed_bytes: int = field(default=0, repr=False)
    _stderr_committed_bytes: int = field(default=0, repr=False)
    _stdout_discarded_bytes: int = field(default=0, repr=False)
    _stderr_discarded_bytes: int = field(default=0, repr=False)
    _stdout_byte_cursor: int = field(default=0, repr=False)
    _stderr_byte_cursor: int = field(default=0, repr=False)
    # Raw RAM offset where persistence stopped for a stream; incremental reads
    # continue from the bounded RAM tail past it, so late output (a failure
    # line after the quota) is not hidden behind the unchanged committed file.
    _stdout_ram_resume: int | None = field(default=None, repr=False)
    _stderr_ram_resume: int | None = field(default=None, repr=False)
    # Cumulative original-stream characters committed to spool (whole lines
    # only; leftover pending is not counted).  Used to set _stdout_ram_resume
    # so the RAM tail picks up exactly after the last committed char without a
    # gap or duplication when quota cuts through a boundary chunk.
    _stdout_spool_original_chars: int = field(default=0, repr=False)
    _stderr_spool_original_chars: int = field(default=0, repr=False)
    # Serializes incremental cursor consumption (snapshot, read, advance).
    _cursor_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    receipt_path: Path | None = field(default=None, repr=False)
    # Why the job was killed by gptme (e.g. the memory guard), for the model.
    kill_reason: str | None = None
    # a kill after the completion message went out (a worker that outlived
    # the job's main process): delivered as a message of its own
    _late_kill_notice: str | None = field(default=None, repr=False)

    def _can_spool_from_process(self) -> bool:
        if (
            self.log_dir is None
            or self._output_callback is not None
            or self.process is None
        ):
            return False
        stdout = getattr(self.process, "stdout", None)
        fileno = getattr(stdout, "fileno", None)
        if stdout is None or not callable(fileno):
            return False
        try:
            fd = fileno()
        except Exception:
            return False
        return isinstance(fd, int) and fd >= 0

    def _open_spools(self) -> None:
        """Open exclusive live stream files when this job owns the pipes."""
        if self._stdout_spool is not None or not self._can_spool_from_process():
            return
        assert self.log_dir is not None
        _seed_conversation_spool(self.conversation_id, self.log_dir)
        if _conversation_spool_remaining(self.conversation_id) <= 0:
            logger.warning(
                "Skipping job #%s spool: conversation retained-stream quota exhausted",
                self.id,
            )
            self._spool_quota_refused = True
            self._note_partial("conversation_quota")
            return
        base = f"job-{self.id}-{int(self.start_time)}"
        stdout_spool: PrivateOutputFile | None = None
        try:
            stdout_spool = open_private_output_file(
                self.log_dir, "shell-jobs", f"{base}.stdout.live"
            )
            self._stderr_spool = open_private_output_file(
                self.log_dir, "shell-jobs", f"{base}.stderr.live"
            )
        except (OSError, UnsafeOutputPathError) as e:
            logger.warning(
                "Could not open background job spool under %s: %s", self.log_dir, e
            )
            if stdout_spool is not None:
                try:
                    stdout_spool.close()
                except OSError as close_error:
                    logger.warning(
                        "Could not close unused background job spool %s: %s",
                        stdout_spool.path,
                        close_error,
                    )
            self._stdout_spool = None
            self._stderr_spool = None
            return
        self._stdout_spool = stdout_spool
        self.log_path = stdout_spool.path
        assert self._stderr_spool is not None
        self.stderr_log_path = self._stderr_spool.path
        self._spool_from_reader = True
        self._spool_queue = queue.Queue(maxsize=_SPOOL_QUEUE_MAX)
        self._spool_writer_thread = threading.Thread(
            target=self._spool_writer_loop,
            daemon=True,
            name=f"gptme-spool-writer-{self.id}",
        )
        self._spool_writer_thread.start()

    def start_reader(self) -> None:
        """Start background thread to read output."""
        self._open_spools()
        self._capture_finished.clear()
        # Sanitize stored output under the initiating chat's configuration.
        context = copy_context()
        self._reader_thread = threading.Thread(
            target=context.run, args=(self._read_output,), daemon=True
        )
        self._reader_thread.start()

    def _append_output(
        self, fd: int, stdout_fd: int, data: bytes, *, final: bool = False
    ) -> None:
        """Decode and append one output chunk while maintaining offsets.

        Uses an incremental UTF-8 decoder so multibyte characters split across
        pipe reads are reassembled correctly instead of replaced. Pass
        ``final=True`` at stream EOF to flush any buffered incomplete sequence.
        """
        decoder = self._stdout_decoder if fd == stdout_fd else self._stderr_decoder
        text = decoder.decode(data, final=final)
        if text or final:
            self._spool_text(fd == stdout_fd, text, final=final)
        if not text:
            return
        with self._buffer_lock:
            if fd == stdout_fd:
                self._stdout_buffer_start += self._append_to_buffer_locked(
                    self.stdout_buffer, text
                )
                if (
                    self._spool_from_reader
                    and not self._spool_persist_stdout
                    and self._stdout_ram_resume is None
                ):
                    # Set resume to the number of original-stream chars that
                    # were actually committed to spool, so the RAM tail picks
                    # up at exactly the right position — no gap (skipped tail)
                    # and no duplication of the already-committed prefix.
                    self._stdout_ram_resume = self._stdout_spool_original_chars
            else:
                self._stderr_buffer_start += self._append_to_buffer_locked(
                    self.stderr_buffer, text
                )
                if (
                    self._spool_from_reader
                    and not self._spool_persist_stderr
                    and self._stderr_ram_resume is None
                ):
                    self._stderr_ram_resume = self._stderr_spool_original_chars

    def _spool_text(self, is_stdout: bool, text: str, *, final: bool) -> None:
        """Commit sanitized complete lines to the live stream before RAM eviction."""
        stream = self._stdout_spool if is_stdout else self._stderr_spool
        if stream is None:
            return
        pending_attr = "_spool_pending_stdout" if is_stdout else "_spool_pending_stderr"
        discard_attr = "_spool_discard_stdout" if is_stdout else "_spool_discard_stderr"
        if getattr(self, discard_attr):
            # The assignment prefix was omitted. Its suffix is unsafe even at EOF;
            # resume only after the newline, with a fresh line's full context.
            _, newline, text = text.partition("\n")
            if not newline:
                return
            setattr(self, discard_attr, False)
        pending = getattr(self, pending_attr) + text
        if final:
            to_write, leftover = pending, ""
        else:
            head, sep, tail = pending.rpartition("\n")
            if not sep:
                if len(pending) > _SPOOL_PENDING_MAX:
                    to_write = "[omitted: pending sanitation window exceeded]\n"
                    leftover = ""
                    setattr(self, discard_attr, True)
                    self._note_partial("sanitation_window")
                else:
                    setattr(self, pending_attr, pending)
                    return
            else:
                to_write, leftover = head + sep, tail
                if len(leftover) > _SPOOL_PENDING_MAX:
                    leftover = ""
                    setattr(self, discard_attr, True)
                    self._note_partial("sanitation_window")
                    to_write += "[omitted: pending sanitation window exceeded]\n"
        setattr(self, pending_attr, leftover)
        if not to_write:
            return
        if is_stdout:
            sanitized, _, _ = _sanitize_job_output(to_write, "", complete=final)
        else:
            _, sanitized, _ = _sanitize_job_output("", to_write, complete=final)
        if sanitized:
            persist_attr = (
                "_spool_persist_stdout" if is_stdout else "_spool_persist_stderr"
            )
            chars_attr = (
                "_stdout_spool_original_chars"
                if is_stdout
                else "_stderr_spool_original_chars"
            )
            content_bytes = self._commit_spool_bytes(
                is_stdout, sanitized.encode("utf-8", "surrogateescape")
            )
            if getattr(self, persist_attr):
                # Full commit: all of to_write was accepted.
                setattr(self, chars_attr, getattr(self, chars_attr) + len(to_write))
            elif content_bytes > 0:
                # Partial quota cut: advance by the bytes of content committed
                # (ASCII bytes ≈ chars; non-ASCII overestimates slightly, which
                # is safe — resume lands inside the already-committed region).
                setattr(self, chars_attr, getattr(self, chars_attr) + content_bytes)

    def _add_discarded(self, is_stdout: bool, count: int) -> None:
        """Record bytes accepted for persistence that were lost to a failure."""
        if count <= 0:
            return
        with self._spool_queue_lock:
            if is_stdout:
                self._stdout_discarded_bytes += count
            else:
                self._stderr_discarded_bytes += count

    def _note_partial(self, reason: str) -> None:
        self._capture_partial = True
        self._partial_reasons.add(reason)

    def _write_spool_payload(
        self, stream: PrivateOutputFile, is_stdout: bool, payload: bytes
    ) -> None:
        stream.append(payload)
        if is_stdout:
            self._stdout_committed_bytes = stream.bytes_written
        else:
            self._stderr_committed_bytes = stream.bytes_written

    def _spool_writer_loop(self) -> None:
        """Persist queued chunks; never runs in the pipe-reader thread."""
        assert self._spool_queue is not None
        try:
            while True:
                try:
                    item = self._spool_queue.get(timeout=0.2)
                except queue.Empty:
                    if self._spool_writer_stop.is_set():
                        break
                    continue
                if item is None:
                    break
                stream, is_stdout, payload = item
                with self._spool_queue_lock:
                    self._spool_queued_bytes = max(
                        0, self._spool_queued_bytes - len(payload)
                    )
                written_before = stream.bytes_written
                try:
                    self._write_spool_payload(stream, is_stdout, payload)
                except OSError as e:
                    logger.warning("Could not append job spool %s: %s", stream.path, e)
                    unwritten = max(
                        0, len(payload) - max(0, stream.bytes_written - written_before)
                    )
                    _release_conversation_spool(self.conversation_id, unwritten)
                    self._add_discarded(is_stdout, unwritten)
                    self._note_partial("write_error")
                    self._spool_persist_stdout = False
                    self._spool_persist_stderr = False
                    while True:
                        try:
                            leftover = self._spool_queue.get_nowait()
                        except queue.Empty:
                            break
                        if leftover is None:
                            break
                        _, leftover_stdout, leftover_payload = leftover
                        with self._spool_queue_lock:
                            self._spool_queued_bytes = max(
                                0, self._spool_queued_bytes - len(leftover_payload)
                            )
                        self._add_discarded(leftover_stdout, len(leftover_payload))
                        _release_conversation_spool(
                            self.conversation_id, len(leftover_payload)
                        )
                    break
        finally:
            self._close_spool_files()
            # A failed writer can exit while the reader is still draining.
            # Final metadata belongs to the reader's stop boundary, not to
            # storage failure. After a join timeout the writer owns publication.
            if self._spool_writer_stop.is_set():
                self._publish_receipt()

    def _commit_spool_bytes(self, is_stdout: bool, data: bytes) -> int:
        """Accept sanitized bytes for persistence without blocking the reader.

        Returns the number of original content bytes committed (0 if none).
        Uses _add_discarded for all counter updates so reader and writer threads
        both hold _spool_queue_lock when mutating the shared discarded counters.
        """
        stream = self._stdout_spool if is_stdout else self._stderr_spool
        persist_attr = "_spool_persist_stdout" if is_stdout else "_spool_persist_stderr"
        accepted_attr = (
            "_stdout_accepted_bytes" if is_stdout else "_stderr_accepted_bytes"
        )
        if stream is None or not data:
            return 0
        if not getattr(self, persist_attr):
            self._add_discarded(is_stdout, len(data))
            return 0

        accepted = getattr(self, accepted_attr)
        marker = _SPOOL_QUOTA_MARKER
        content_quota = max(0, _SPOOL_STREAM_QUOTA - len(marker))
        stream_room = max(0, content_quota - accepted)
        conv_room = _conversation_spool_remaining(self.conversation_id)
        room = min(stream_room, conv_room)
        payload = data
        discarded = 0
        # content_bytes: how many bytes of original `data` will be committed
        content_bytes = len(data)
        stream_limited = stream_room <= conv_room
        if len(data) > room:
            if room > 0:
                payload = data[:room] + (marker if stream_limited else b"")
                discarded = len(data) - room
                content_bytes = room
            elif stream_limited:
                payload = marker
                discarded = len(data)
                content_bytes = 0
            else:
                setattr(self, persist_attr, False)
                self._note_partial("conversation_quota")
                self._add_discarded(is_stdout, len(data))
                return 0
            setattr(self, persist_attr, False)
            self._note_partial("quota" if stream_limited else "conversation_quota")

        granted = _try_reserve_conversation_spool(self.conversation_id, len(payload))
        if granted < len(payload):
            dropped_tail = len(payload) - granted
            payload = payload[:granted]
            discarded += dropped_tail
            setattr(self, persist_attr, False)
            self._note_partial("conversation_quota")
            # Payload is data[:content_bytes] + marker (or just data for full).
            # After trim, only min(granted, content_bytes) bytes of content remain.
            content_bytes = min(content_bytes, granted)
            if not payload:
                self._add_discarded(is_stdout, len(data))
                return 0

        if discarded:
            self._add_discarded(is_stdout, discarded)

        if self._spool_queue is not None:
            with self._spool_queue_lock:
                if self._spool_queued_bytes + len(payload) > _SPOOL_QUEUE_MAX_BYTES:
                    queued = False
                else:
                    self._spool_queued_bytes += len(payload)
                    queued = True
            if not queued:
                _release_conversation_spool(self.conversation_id, len(payload))
                self._note_partial("queue_full")
                setattr(self, persist_attr, False)
                self._add_discarded(is_stdout, len(payload))
                return 0

            def _unreserve_queue() -> None:
                with self._spool_queue_lock:
                    self._spool_queued_bytes = max(
                        0, self._spool_queued_bytes - len(payload)
                    )
                _release_conversation_spool(self.conversation_id, len(payload))
                self._note_partial("queue_full")
                setattr(self, persist_attr, False)
                self._add_discarded(is_stdout, len(payload))

            try:
                self._spool_queue.put_nowait((stream, is_stdout, payload))
            except queue.Full:
                writer = self._spool_writer_thread
                if writer is None or not writer.is_alive():
                    _unreserve_queue()
                    return 0
                try:
                    self._spool_queue.put(
                        (stream, is_stdout, payload),
                        timeout=_SPOOL_QUEUE_PUT_TIMEOUT,
                    )
                except queue.Full:
                    _unreserve_queue()
                    return 0
        else:
            try:
                self._write_spool_payload(stream, is_stdout, payload)
            except OSError as e:
                _release_conversation_spool(self.conversation_id, len(payload))
                logger.warning("Could not append job spool %s: %s", stream.path, e)
                self._note_partial("write_error")
                setattr(self, persist_attr, False)
                try:
                    stream.close()
                except OSError:
                    pass
                if is_stdout:
                    self._stdout_spool = None
                else:
                    self._stderr_spool = None
                return 0
        setattr(self, accepted_attr, accepted + len(payload))
        return content_bytes

    def _close_spool_files(self) -> None:
        for stream in (self._stdout_spool, self._stderr_spool):
            if stream is None:
                continue
            try:
                stream.close()
            except OSError as e:
                logger.warning(
                    "Could not close background job spool %s: %s",
                    stream.path,
                    e,
                )
                self._note_partial("write_error")

    def _finalize_spools(self) -> None:
        """Stop the writer, then close live files and publish receipt metadata."""
        queue_obj = self._spool_queue
        writer = self._spool_writer_thread
        self._spool_writer_stop.set()
        if queue_obj is not None:
            try:
                queue_obj.put_nowait(None)
            except queue.Full:
                # The stop event also ends the writer after it drains the queue.
                # Failure to enqueue a sentinel is not itself a timeout or loss.
                pass
        if writer is not None:
            writer.join(timeout=_SPOOL_WRITER_JOIN_TIMEOUT)
            if writer.is_alive():
                self._note_partial("writer_timeout")
                return
        self._close_spool_files()
        self._publish_receipt()

    def _publish_receipt(self) -> None:
        """Atomically publish final capture metadata after stream close."""
        if self.log_dir is None or not self._spool_from_reader or self.receipt_path:
            return
        capture_state = "partial" if self._capture_partial else "complete"
        exit_code = getattr(self.process, "returncode", None)
        if not isinstance(exit_code, int):
            exit_code = None
        receipt = {
            "job_id": self.id,
            "byte_count_basis": "sanitized_utf8_representation",
            "capture_state": capture_state,
            "partial_reasons": sorted(self._partial_reasons),
            "exit_code": exit_code,
            "stdout": {
                "path": str(self.log_path) if self.log_path else None,
                "committed_bytes": self._stdout_committed_bytes,
                "accepted_bytes": self._stdout_accepted_bytes,
                "discarded_bytes": self._stdout_discarded_bytes,
                "cursor_bytes": self._stdout_byte_cursor,
                "raw_observed_bytes": None,
                "raw_accepted_bytes": None,
                "raw_pending_bytes": None,
                "raw_discarded_bytes": None,
            },
            "stderr": {
                "path": str(self.stderr_log_path) if self.stderr_log_path else None,
                "committed_bytes": self._stderr_committed_bytes,
                "accepted_bytes": self._stderr_accepted_bytes,
                "discarded_bytes": self._stderr_discarded_bytes,
                "cursor_bytes": self._stderr_byte_cursor,
                "raw_observed_bytes": None,
                "raw_accepted_bytes": None,
                "raw_pending_bytes": None,
                "raw_discarded_bytes": None,
            },
        }
        payload = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
        dest = (
            self.log_dir
            / TOOL_OUTPUTS_DIRNAME
            / "shell-jobs"
            / f"job-{self.id}-{int(self.start_time)}.receipt.json"
        )
        try:
            # Temp-file + exclusive publish so readers never see an empty final name.
            self.receipt_path = _write_private_output(self.log_dir, dest, payload)
        except (OSError, UnsafeOutputPathError) as e:
            logger.warning(
                "Could not publish background job receipt under %s: %s",
                self.log_dir,
                e,
            )
            self._note_partial("receipt_write")

    def _read_committed_range(self, is_stdout: bool, start: int, end: int) -> bytes:
        """Read committed retained bytes ``[start, end)`` without blocking the writer."""
        length = end - start
        if length <= 0:
            return b""
        stream = self._stdout_spool if is_stdout else self._stderr_spool
        path = self.log_path if is_stdout else self.stderr_log_path
        if stream is not None and not stream._closed:
            pread = getattr(os, "pread", None)
            if pread is not None:
                try:
                    return pread(stream._fd, length, start)
                except OSError:
                    pass
        if path is None:
            return b""
        with open(path, "rb") as handle:
            handle.seek(start)
            return handle.read(length)

    def _consume_committed_text(self, is_stdout: bool) -> str:
        """Advance the per-stream byte cursor and return newly committed text."""
        committed = (
            self._stdout_committed_bytes if is_stdout else self._stderr_committed_bytes
        )
        cursor_attr = "_stdout_byte_cursor" if is_stdout else "_stderr_byte_cursor"
        cursor = getattr(self, cursor_attr)
        if committed <= cursor:
            return ""
        display_limit = (
            _STDOUT_DISPLAY_CHARS if is_stdout else _STDERR_DISPLAY_CHARS
        ) * 4
        start = cursor
        if committed - start > display_limit:
            start = committed - display_limit
        data = self._read_committed_range(is_stdout, start, committed)
        setattr(self, cursor_attr, committed)
        return data.decode("utf-8", "replace")

    def _consume_incremental_text(self, is_stdout: bool) -> str:
        """Return new output for one stream: committed bytes, then the RAM tail.

        Once persistence stops (quota, queue, write error) the file boundary no
        longer moves, so output after it is served from the bounded RAM buffer.
        The tail is delivered only after the writer has caught up, which keeps
        the order of committed bytes and RAM text.
        """
        with self._cursor_lock:
            text = self._consume_committed_text(is_stdout)
            resume = self._stdout_ram_resume if is_stdout else self._stderr_ram_resume
            if resume is None:
                return text
            accepted = (
                self._stdout_accepted_bytes
                if is_stdout
                else self._stderr_accepted_bytes
            )
            committed = (
                self._stdout_committed_bytes
                if is_stdout
                else self._stderr_committed_bytes
            )
            if committed < accepted and "write_error" not in self._partial_reasons:
                return text
            with self._buffer_lock:
                if is_stdout:
                    joined = "".join(self.stdout_buffer)
                    buffer_start = self._stdout_buffer_start
                    offset = max(self._stdout_read_offset, resume)
                    self._stdout_read_offset = buffer_start + len(joined)
                else:
                    joined = "".join(self.stderr_buffer)
                    buffer_start = self._stderr_buffer_start
                    offset = max(self._stderr_read_offset, resume)
                    self._stderr_read_offset = buffer_start + len(joined)
            raw_start = max(offset - buffer_start, 0)
            if raw_start >= len(joined):
                # Nothing new since the last poll; do not replay a partial line.
                return text
            # Extend back to the beginning of the containing line so the
            # downstream redactor (_sanitize_job_output in format_job_output)
            # has full assignment context.  Without this, a poll boundary that
            # splits "API_KEY=<value>" causes the second slice to start with
            # the opaque value, which the redactor cannot recognise.
            if raw_start > 0:
                line_start = joined.rfind("\n", 0, raw_start) + 1
            else:
                line_start = raw_start
            # If the containing line began before the buffer (evicted), skip
            # the orphan prefix — the redactor also lacks context for it.
            if line_start == 0 and buffer_start > 0:
                first_nl = joined.find("\n")
                line_start = (first_nl + 1) if first_nl >= 0 else len(joined)
            return text + joined[line_start:]

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
                        self._note_partial("read_error")
                        open_fds.discard(fd)
                        continue
                    if not raw:
                        open_fds.discard(fd)
                        self._append_output(fd, stdout_fd, b"", final=True)
                        continue
                    read_any = True
                    self._append_output(fd, stdout_fd, raw)
                if _is_windows and not read_any:
                    time.sleep(0.1)
            except (OSError, ValueError):
                break

        # EOF is not guaranteed: drain limits, read errors and forced stops can
        # also end the reader. Preserve any pending bytes before publishing output.
        if open_fds:
            self._note_partial("stream_not_drained")
        for fd in (stdout_fd, stderr_fd):
            if fd >= 0:
                self._append_output(fd, stdout_fd, b"", final=True)
        self._finalize_spools()
        # Consumers can render the notification before this thread returns.
        # Capture is final now, even though the notifying thread is still alive.
        with _job_log_lock:
            self._capture_finished.set()
        if self._snapshot_stream is not None:
            _write_job_log(self, *self.get_output(), final=False, complete=True)
        _notify_completion(self)

    def _append_to_buffer_locked(self, buffer: list[str], data: str) -> int:
        """Append data to a locked buffer, enforcing the size limit.

        Oversized single appends (including promoted-command completions) keep
        only the tail so one 32 MiB result cannot pin the registry.
        """
        if not data:
            return 0
        removed_size = 0
        if len(data) > _MAX_BUFFER_SIZE:
            removed_size += len(data) - _MAX_BUFFER_SIZE
            data = data[-_MAX_BUFFER_SIZE:]
        buffer.append(data)
        total_size = sum(len(s) for s in buffer)
        while total_size > _MAX_BUFFER_SIZE and len(buffer) > 1:
            removed = buffer.pop(0)
            total_size -= len(removed)
            removed_size += len(removed)
        return removed_size

    def _append_to_buffer(self, buffer: list[str], data: str) -> int:
        """Append data to a buffer, enforcing size limit thread-safely."""
        with self._buffer_lock:
            return self._append_to_buffer_locked(buffer, data)

    def get_output(
        self, *, incremental: bool = False, line_context: bool = False
    ) -> tuple[str, str]:
        """Get accumulated stdout and stderr, optionally since the last read."""
        if incremental and self._spool_from_reader:
            # Independent committed-byte cursors: RAM character offsets can
            # lag or skip prefixes the live files already retained.
            return (
                self._consume_incremental_text(True),
                self._consume_incremental_text(False),
            )
        with self._buffer_lock:
            if self._output_callback is not None:
                stdout, stderr = self._output_callback()
            else:
                stdout = "".join(self.stdout_buffer)
                stderr = "".join(self.stderr_buffer)
            if not incremental:
                return stdout, stderr

            stdout_start = max(self._stdout_read_offset - self._stdout_buffer_start, 0)
            stderr_start = max(self._stderr_read_offset - self._stderr_buffer_start, 0)
            # Redaction needs the assignment at the beginning of a line even
            # when a previous --new read stopped halfway through that line.
            # Replay that line only when new data arrived; offsets stay raw.
            if line_context:
                if stdout_start < len(stdout):
                    stdout_start = stdout.rfind("\n", 0, stdout_start) + 1
                if stderr_start < len(stderr):
                    stderr_start = stderr.rfind("\n", 0, stderr_start) + 1
            new_stdout = stdout[stdout_start:]
            new_stderr = stderr[stderr_start:]
            if line_context:
                new_stdout, new_stderr = _omit_evicted_line(
                    self,
                    new_stdout,
                    new_stderr,
                    stdout_boundary=stdout_start == 0,
                    stderr_boundary=stderr_start == 0,
                )
            self._stdout_read_offset = self._stdout_buffer_start + len(stdout)
            self._stderr_read_offset = self._stderr_buffer_start + len(stderr)
            return new_stdout, new_stderr

    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None

    def is_output_complete(self) -> bool:
        """Check whether the reader has drained all inherited output pipes."""
        return (
            self._capture_finished.is_set()
            or self._reader_thread is None
            or not self._reader_thread.is_alive()
        )

    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def kill(self) -> None:
        """Terminate the background job and its process group."""
        if self.process.poll() is None:
            try:
                if self._kill_callback is not None:
                    self._kill_callback()
                elif _is_windows:
                    self.process.terminate()
                else:
                    assert self.process_group_id is not None
                    os.killpg(self.process_group_id, signal.SIGTERM)
                self.process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                if self._kill_callback is not None:
                    self._kill_callback()
                elif _is_windows:
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
        if self._close_callback is not None:
            self._close_callback()


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


def _current_log_dir() -> Path | None:
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    logdir = getattr(manager, "logdir", None) if manager else None
    return Path(logdir) if logdir else None


def control_commands_help(job_id: int) -> str:
    """Describe the job-control calls for ``job_id`` exactly as they must be made."""
    return (
        "Manage it with these gptme job-control commands. Each must be the "
        "*entire* shell command (they are not bash commands, so they cannot be "
        "piped, chained, or used inside a script):\n"
        f"- `output {job_id}` - show recent output (`output {job_id} --new` for "
        "output since the last check)\n"
        f"- `wait {job_id} [timeout]` - block until it finishes, e.g. "
        f"`wait {job_id} 10m`, then show its output\n"
        f"- `kill {job_id}` - terminate it\n"
        "- `jobs` - list background jobs"
    )


# Serializes log writes so a live snapshot never overwrites the final log.
_job_log_lock = threading.Lock()


def _sanitize_job_output(
    stdout: str, stderr: str, *, complete: bool = False
) -> tuple[str, str, int]:
    """Sanitize at presentation/storage, leaving raw buffer offsets intact."""
    from ..config import get_config
    from ..util.redact import redact_secret_values_from_output, redact_secrets_from_text
    from .shell import strip_ansi_codes

    stdout, stderr = strip_ansi_codes(stdout), strip_ansi_codes(stderr)
    if not get_config().get_env_bool("GPTME_SHELL_REDACT_SECRETS", True):
        return stdout, stderr, 0

    # Live reads can stop before an opaque value reaches the shape threshold.
    # Conservatively hide a trailing incomplete assignment before publication.
    def hide_partial(text: str) -> str:
        head, sep, tail = text.rpartition("\n")
        return head + sep + redact_secrets_from_text(tail)

    if not complete:
        stdout, stderr = hide_partial(stdout), hide_partial(stderr)
    stdout, n_out = redact_secret_values_from_output(stdout)
    stderr, n_err = redact_secret_values_from_output(stderr)
    return stdout, stderr, n_out + n_err


def _write_job_log(
    job: BackgroundJob,
    stdout: str,
    stderr: str,
    *,
    final: bool,
    complete: bool = False,
) -> Path | None:
    """Write a job's output to its log file; return the path, or None.

    A non-final (live snapshot) write is skipped once the final, complete
    output has been written, and the existing log path is returned instead.
    """
    if job.log_dir is None:
        return None
    with _job_log_lock:
        if job._final_log_written or job._snapshot_log_finished:
            return job.log_path
        if not final and job._capture_finished.is_set():
            # A preview may have sampled RAM before the reader finished.
            stdout, stderr = job.get_output()
            complete = True
        path = _write_job_log_locked(
            job, stdout, stderr, final=final, complete=complete
        )
        if path is not None and not job._spool_from_reader:
            if final:
                job._final_log_written = True
            elif job._capture_finished.is_set():
                job._snapshot_log_finished = True
        return path


def _write_job_log_locked(
    job: BackgroundJob,
    stdout: str,
    stderr: str,
    *,
    final: bool,
    complete: bool = False,
) -> Path | None:
    assert job.log_dir is not None
    if job._spool_from_reader:
        # Only the reader owns live descriptors, including final flush/close.
        # Never replace a partial live spool with a RAM snapshot.
        return job.log_path
    if job._spool_quota_refused:
        # Conversation allowance was already exhausted; do not write more logs.
        return job.log_path
    if not final:
        stdout, stderr = _omit_evicted_line(job, stdout, stderr)
    stdout, stderr, _ = _sanitize_job_output(stdout, stderr, complete=final or complete)
    dropped = job._stdout_buffer_start + job._stderr_buffer_start
    parts = [f"$ {job.command}\n"]
    if dropped and not final:
        parts.append(
            f"[earliest {dropped} chars were dropped from the in-memory buffer]\n"
        )
    parts.append(f"\n--- stdout ---\n{stdout}")
    if stderr:
        parts.append(f"\n--- stderr ---\n{stderr}")
    payload = "".join(parts).encode("utf-8", "surrogateescape")
    written = False
    try:
        if job._snapshot_stream is None:
            name = f"job-{job.id}-{int(job.start_time)}.log"
            job._snapshot_stream = open_private_output_file(
                job.log_dir, "shell-jobs", name
            )
            job.log_path = job._snapshot_stream.path
        job._snapshot_stream.replace(payload)
        written = True
    except (OSError, UnsafeOutputPathError) as e:
        logger.warning("Could not write background job log %s: %s", job.log_path, e)
        return None
    finally:
        if (
            final or job._capture_finished.is_set()
        ) and job._snapshot_stream is not None:
            try:
                job._snapshot_stream.close()
            except OSError as e:
                logger.warning(
                    "Could not close background job log %s: %s", job.log_path, e
                )
            if not written:
                # A later preview may retry, but never through this closed fd.
                job._snapshot_stream = None
    return job.log_path


def _omit_evicted_line(
    job: BackgroundJob,
    stdout: str,
    stderr: str,
    *,
    stdout_boundary: bool = True,
    stderr_boundary: bool = True,
) -> tuple[str, str]:
    """Discard a first line whose assignment context may have been evicted."""
    from ..config import get_config

    if get_config().get_env_bool("GPTME_SHELL_REDACT_SECRETS", True):
        if stdout and stdout_boundary and job._stdout_buffer_start:
            stdout = (
                "[first retained line omitted: its prefix was evicted]\n"
                + stdout.partition("\n")[2]
            )
        if stderr and stderr_boundary and job._stderr_buffer_start:
            stderr = (
                "[first retained line omitted: its prefix was evicted]\n"
                + stderr.partition("\n")[2]
            )
    return stdout, stderr


def format_job_output(
    job: BackgroundJob,
    stdout: str,
    stderr: str,
    *,
    omit_evicted_prefix: bool = True,
    complete: bool = False,
) -> str:
    """Render the tails of a job's output, naming the full log if truncated."""
    if omit_evicted_prefix:
        stdout, stderr = _omit_evicted_line(job, stdout, stderr)
    stdout, stderr, redacted_lines = _sanitize_job_output(
        stdout, stderr, complete=complete
    )
    blocks: list[str] = []
    truncated = bool(job._stdout_buffer_start or job._stderr_buffer_start)
    for name, text, limit in (
        ("stdout", stdout, _STDOUT_DISPLAY_CHARS),
        ("stderr", stderr, _STDERR_DISPLAY_CHARS),
    ):
        if not text:
            continue
        if len(text) > limit:
            truncated = True
            text = f"...(truncated, showing last {limit} of {len(text)} chars)...\n{text[-limit:]}"
        blocks.append(md_codeblock(name, text))
    if truncated or job.log_path is not None:
        log_path = _write_job_log(
            job, *job.get_output(), final=False, complete=complete
        )
        dropped = job._stdout_buffer_start + job._stderr_buffer_start
        if job._spool_from_reader:
            if job._capture_partial:
                label = "Partial output (capture omitted data)"
            elif job._spool_pending_stdout or job._spool_pending_stderr:
                label = "Partial output (incomplete line pending sanitation)"
            else:
                label = "Full output"
            paths = [f"stdout: `{log_path}`"] if log_path is not None else []
            if job.stderr_log_path is not None:
                paths.append(f"stderr: `{job.stderr_log_path}`")
            if paths:
                blocks.append(label + ":\n" + "\n".join(paths))
        elif log_path is not None and (job._final_log_written or not dropped):
            blocks.append(f"Full output: `{log_path}`")
        elif log_path is not None:
            # The in-memory buffer already discarded the earliest output.
            blocks.append(
                f"Output log (earliest {dropped} chars were not retained): `{log_path}`"
            )
    if redacted_lines:
        blocks.append(
            f"Redacted secret values on {redacted_lines} output lines "
            "(set GPTME_SHELL_REDACT_SECRETS=0 to disable)"
        )
    return "\n\n".join(blocks)


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


def _record_kill_reason(job: BackgroundJob, reason: str) -> None:
    """Tell the model why gptme killed the job: in its completion message, or,
    if that already went out, in a message of its own."""
    with _job_lock:  # the completion hook takes it too: no delivery meanwhile
        with _completion_queue.mutex:
            undelivered = any(queued is job for queued in _completion_queue.queue)
        if not job._completion_notified or undelivered:
            job.kill_reason = reason
            return
        if _background_jobs.get(job.conversation_id, {}).get(job.id) is not job:
            return
        job._late_kill_notice = reason
        _completion_queue.put(job)


def _jobs_for(conversation_id: str | None) -> dict[int, BackgroundJob]:
    return _background_jobs.setdefault(conversation_id, {})


def _get_background_job(
    conversation_id: str | None, job_id: int
) -> BackgroundJob | None:
    with _job_lock:
        return _background_jobs.get(conversation_id, {}).get(job_id)


def register_background_job(
    command: str,
    process: Any,
    *,
    start_time: float | None = None,
    kill_callback: Callable[[], None] | None = None,
    close_callback: Callable[[], None] | None = None,
    output_callback: Callable[[], tuple[str, str]] | None = None,
) -> BackgroundJob:
    """Register an already-running command without starting an output reader."""
    conversation_id = _current_conversation_id()
    with _job_lock:
        job = BackgroundJob(
            id=_get_next_job_id_locked(conversation_id),
            command=command,
            process=process,
            start_time=time.time() if start_time is None else start_time,
            conversation_id=conversation_id,
            _kill_callback=kill_callback,
            _close_callback=close_callback,
            _output_callback=output_callback,
            log_dir=_current_log_dir(),
        )
        _jobs_for(conversation_id)[job.id] = job
    return job


def complete_background_job(job: BackgroundJob, stdout: str, stderr: str) -> None:
    """Store final output and publish one completion for a registered job."""
    if len(stdout) > _STDOUT_DISPLAY_CHARS or len(stderr) > _STDERR_DISPLAY_CHARS:
        # Persist the complete output now: the in-memory buffer keeps only
        # the last _MAX_BUFFER_SIZE chars, and messages show only a tail.
        _write_job_log(job, stdout, stderr, final=True)
    with job._buffer_lock:
        job._output_callback = None
        if stdout:
            job._stdout_buffer_start += job._append_to_buffer_locked(
                job.stdout_buffer, stdout
            )
        if stderr:
            job._stderr_buffer_start += job._append_to_buffer_locked(
                job.stderr_buffer, stderr
            )
    # A failed final write can be retried by completion rendering. Publish the
    # finished capture only after RAM holds the final output, so that retry
    # closes its snapshot and later previews do not reopen it.
    with _job_log_lock:
        job._capture_finished.set()
    _notify_completion(job)


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
    # A subprocess subagent's parent records the group so it can force the
    # whole job down if the child CLI ignores SIGTERM. Imported lazily to
    # avoid a circular import (shell imports this module at load time).
    from .shell import _record_shell_pgid

    _record_shell_pgid(process.pid)

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
            log_dir=_current_log_dir(),
        )
        _jobs_for(conversation_id)[job_id] = job
    if not _is_windows:
        from .shell_memory import Target, group_alive, kill_group, watch

        def kill_for_memory() -> None:
            # the whole group: workers can outlive a leader that exits on TERM
            kill_group(process.pid)
            job.kill()

        watch(
            Target(
                pgid=process.pid,
                label=f"background job #{job_id}",
                kill=kill_for_memory,
                alive=lambda: group_alive(process.pid),
                on_killed=lambda reason: _record_kill_reason(job, reason),
            )
        )
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
            with _conversation_spool_lock:
                _conversation_spool_bytes.clear()
                _conversation_spool_seeded.clear()
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
    if notice := job._late_kill_notice:
        job._late_kill_notice = None
        return Message(
            "system",
            f"Processes left over from background shell job #{job.id} "
            f"(`{job.command}`), which had already finished: {notice}",
        )
    status = f"exit code {job.process.returncode}"
    complete = not job.is_running() and job.is_output_complete()
    stdout, stderr = job.get_output()
    details = format_job_output(job, stdout, stderr, complete=complete)
    suffix = f"\n\n{details}" if details else "\n\nNo output."
    if job.kill_reason:
        suffix = f"\n\n{job.kill_reason}{suffix}"
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
    *,
    subagent_notifications: bool | Callable[[], bool] = True,
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
        notify = (
            subagent_notifications()
            if callable(subagent_notifications)
            else subagent_notifications
        )
        if notify and (
            not subagent_completions.empty() or not subagent_progress.empty()
        ):
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
    *,
    subagent_notifications: bool | Callable[[], bool] = True,
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
    unsubscribe = None
    logdir = getattr(manager, "logdir", None)
    if callable(subagent_notifications) and logdir is not None:
        from ..util.notification_ready import subscribe

        def wake_for_child() -> None:
            with _job_lock:
                condition = _job_conditions.setdefault(
                    conversation_id, threading.Condition(_job_lock)
                )
                condition.notify_all()

        unsubscribe = subscribe(logdir, wake_for_child)
    set_interruptible()
    try:
        while True:
            with _job_lock:
                messages = list(background_job_completion_hook(manager))
                pending = _pending_wait_jobs(conversation_id)
                if messages or not pending:
                    break
            if incoming := _background_wait_input(
                manager, subagent_notifications=subagent_notifications
            ):
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
                # Recheck child readiness under the same lock used by its
                # wake callback, closing the check-to-condition-wait race.
                if callable(subagent_notifications) and subagent_notifications():
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
        if unsubscribe is not None:
            unsubscribe()
    if messages:
        yield from messages
        yield StopPropagation()


# Background command handlers


def execute_bg_command(
    command: str, memory_limit: int | None = None
) -> Generator[Message, None, None]:
    """Start a command as a background job."""
    from .shell_validation import format_denylist_denial, is_denylisted

    if not command.strip():
        yield Message("system", "Usage: `bg <command>`\n\nExample: `bg npm run dev`")
        return

    # Check if command is denylisted - blocked even for background jobs
    is_denied, deny_reason, matched_cmd = is_denylisted(command)
    if is_denied:
        yield policy_block_message(
            "denylist-bg",
            format_denylist_denial(
                matched_cmd, deny_reason, label="Background command denied"
            ),
        )
        return

    job = start_background_job(command, memory_limit=memory_limit)
    yield Message(
        "system",
        f"Started background job **#{job.id}**: `{command}`\n\n"
        + control_commands_help(job.id),
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

    from ..config import get_config

    # Sample completion before reading: a live snapshot must remain conservative
    # even if the reader finishes immediately after we captured it.
    complete = not job.is_running() and job.is_output_complete()
    stdout, stderr = job.get_output(
        incremental=incremental,
        line_context=bool(
            get_config().get_env_bool("GPTME_SHELL_REDACT_SECRETS", True)
        ),
    )
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

    if stdout or stderr:
        # Incremental get_output already omits only a slice at the eviction
        # boundary; later slices must keep their first new line.
        msg += (
            format_job_output(
                job,
                stdout,
                stderr,
                omit_evicted_prefix=not incremental,
                complete=complete,
            )
            + "\n\n"
        )
    else:
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
