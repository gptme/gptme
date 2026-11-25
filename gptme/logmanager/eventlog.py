"""Append-only event log for session durability.

Provides an append-only JSONL event log alongside the primary
``conversation.jsonl``.  Periodic checkpoint cells (every
:py:data:`CHECKPOINT_INTERVAL` events) allow efficient recovery of the
message list from the event log alone — skipping replay from event 1.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import tempfile
import threading
import weakref
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .durability import sync_directory

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ..message import Message

try:
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    msvcrt: Any = importlib.import_module("msvcrt")
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

_event_log_thread_locks_guard = threading.Lock()
_event_log_thread_locks: weakref.WeakValueDictionary[Path, threading.Lock] = (
    weakref.WeakValueDictionary()
)

logger = logging.getLogger(__name__)

# ── file name and checkpoint interval ────────────────────────────────
EVENT_LOG_NAME = "events.jsonl"
CHECKPOINT_INTERVAL = 50

# ── event type constants ─────────────────────────────────────────────
EVENT_MESSAGE_APPEND = "message_append"
EVENT_CHECKPOINT = "checkpoint"
EVENT_UNDO = "undo"
EVENT_MESSAGE_EDIT = "message_edit"


# ── helpers ──────────────────────────────────────────────────────────


def _event_log_path(logdir: Path) -> Path:
    return logdir / EVENT_LOG_NAME


def _make_event(seq: int, type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": seq,
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": type,
        "payload": payload,
    }


# ── public API ───────────────────────────────────────────────────────


def _get_event_log_thread_lock(path: Path) -> threading.Lock:
    """Return the in-process lock shared by users of one recovery log."""
    key = path.resolve()
    with _event_log_thread_locks_guard:
        lock = _event_log_thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _event_log_thread_locks[key] = lock
        return lock


@contextmanager
def _event_log_lock(logdir: Path) -> Iterator[None]:
    """Serialize event appends and compaction for one recovery log."""
    path = _event_log_path(logdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / f".{path.name}.lock"
    thread_lock = _get_event_log_thread_lock(path)
    with thread_lock, lock_path.open("a+b") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
            # Append mode is needed to atomically create the sidecar, but Windows
            # append semantics ignore seek() for writes.  Only initialize an empty
            # sidecar so repeated acquisitions do not grow it indefinitely.
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _append_event_unlocked(logdir: Path, event: dict[str, Any]) -> None:
    path = _event_log_path(logdir)
    # Guard against a damaged tail with no terminating newline: a missing
    # newline would concatenate the next event with the malformed record,
    # making both unparseable.
    if path.exists():
        size = path.stat().st_size
        if size > 0:
            with open(path, "rb") as fb:
                fb.seek(size - 1)
                if fb.read(1) != b"\n":
                    with open(path, "ab") as fa:
                        fa.write(b"\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def append_event(logdir: Path, event: dict[str, Any]) -> None:
    """Append a single event record to the event log."""
    with _event_log_lock(logdir):
        _append_event_unlocked(logdir, event)


def append_next_event(
    logdir: Path, build_event: Callable[[int], dict[str, Any]]
) -> dict[str, Any]:
    """Assign the next sequence and append an event under one lock."""
    with _event_log_lock(logdir):
        event = build_event(sequence_number(logdir))
        _append_event_unlocked(logdir, event)
    return event


def sync_events(logdir: Path) -> None:
    """Sync the current recovery file while excluding checkpoint replacement."""
    with _event_log_lock(logdir):
        path = _event_log_path(logdir)
        if path.exists():
            with path.open("rb") as file:
                os.fsync(file.fileno())


def _compact_events_unlocked(logdir: Path) -> None:
    """Drop events superseded by the latest checkpoint; caller holds lock."""
    path = _event_log_path(logdir)
    events = read_events(logdir)
    checkpoint = find_latest_checkpoint(events)
    if checkpoint is None:
        return

    retained = [event for event in events if event["seq"] >= checkpoint["seq"]]
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        try:
            tmp_file = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            # fdopen did not take ownership, so the raw descriptor is ours.
            os.close(fd)
            raise
        # From here the file object owns fd and closes it exactly once; never
        # close fd directly, or a concurrently opened file reusing the number
        # gets closed instead.
        with tmp_file as f:
            f.writelines(json.dumps(event, default=str) + "\n" for event in retained)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        sync_directory(path.parent)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def compact_events(logdir: Path) -> None:
    """Drop events superseded by the latest checkpoint.

    Checkpoints contain the full message state, so events before the newest one
    are redundant for recovery.  Appends and compaction share a per-log lock,
    and replacement is atomic, so neither partial files nor lost appends are
    observable.
    """
    with _event_log_lock(logdir):
        _compact_events_unlocked(logdir)


def _iter_event_lines(path: Path) -> Iterator[str]:
    """Yield non-empty lines, skipping records with undecodable UTF-8 bytes.

    Decoding is per line and strict: a tail truncated inside a multi-byte
    sequence is skipped like a malformed record rather than substituted with
    U+FFFD, which could turn damaged bytes into syntactically valid JSON that
    ``read_events()`` would then replay as real conversation content.
    """
    with open(path, "rb") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield line.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("Skipping undecodable event line in %s", path)


def _parse_event(line: str) -> dict[str, Any] | None:
    """Parse one event line, or return *None* if it is not a usable event.

    Well-formed JSON of the wrong shape (a list, a scalar, or an object without
    an integer ``seq``) is rejected like malformed JSON: every consumer indexes
    ``event["seq"]`` and calls ``event.get``.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    seq = event.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return None
    if not _payload_shape_ok(event.get("type"), event.get("payload", _MISSING)):
        return None
    return event


_MISSING = object()


def _payload_shape_ok(event_type: Any, payload: Any) -> bool:
    """Check the payload fields recovery and compaction index, per event type.

    *payload* is ``_MISSING`` when the row has no ``payload`` key, which differs
    from an explicit ``null``: consumers default a missing payload but call
    ``.get`` on a present one.
    """
    if event_type == EVENT_MESSAGE_APPEND:
        return isinstance(payload, dict) and isinstance(payload.get("message"), dict)
    if event_type in (EVENT_CHECKPOINT, EVENT_MESSAGE_EDIT):
        if not isinstance(payload, dict):
            return False
        messages = payload.get("messages")
        return isinstance(messages, list) and all(isinstance(m, dict) for m in messages)
    if event_type == EVENT_UNDO:
        if payload is _MISSING:
            return True
        if not isinstance(payload, dict):
            return False
        n = payload.get("n", 1)
        return isinstance(n, int) and not isinstance(n, bool)
    return True


def _latest_damaged_snapshot_seq(logdir: Path) -> int | None:
    """Seq of the newest checkpoint/edit row that has a seq but an unusable
    payload. Such a row replaced the whole message list, so skipping it would
    silently drop the history it held."""
    path = _event_log_path(logdir)
    if not path.exists():
        return None
    latest: int | None = None
    for line in _iter_event_lines(path):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") not in (
            EVENT_CHECKPOINT,
            EVENT_MESSAGE_EDIT,
        ):
            continue
        seq = event.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            continue
        if not _payload_shape_ok(event["type"], event.get("payload", _MISSING)):
            latest = seq if latest is None else max(latest, seq)
    return latest


def read_events(logdir: Path) -> list[dict[str, Any]]:
    """Read all events from the event log, oldest first.

    Returns an empty list if no event log exists.  Malformed lines and rows
    that are not objects with an integer ``seq`` are skipped.
    """
    path = _event_log_path(logdir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in _iter_event_lines(path):
        event = _parse_event(line)
        if event is None:
            logger.warning("Skipping malformed event line in %s", path)
            continue
        events.append(event)
    return events


def sequence_number(logdir: Path) -> int:
    """Return the next sequence number for a new event."""
    path = _event_log_path(logdir)
    if not path.exists():
        return 1
    # Read only the last decodable non-empty line to avoid O(n) full-file parse
    last_line = ""
    for line in _iter_event_lines(path):
        last_line = line
    if not last_line:
        return 1
    last_event = _parse_event(last_line)
    if last_event is not None:
        return last_event["seq"] + 1
    # Last line is damaged, or a valid JSON record without an integer seq —
    # scan the remaining valid events for the maximum seq so the next
    # sequence is never below the checkpoint's seq (which would cause
    # recover_messages() to discard the new event as pre-checkpoint).
    seqs = [e["seq"] for e in read_events(logdir)]
    if seqs:
        return max(seqs) + 1
    return 1


def write_checkpoint(
    logdir: Path,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assign, write, and compact a checkpoint under one lock.

    Returns the written checkpoint event.
    """
    with _event_log_lock(logdir):
        event = _make_event(
            sequence_number(logdir), EVENT_CHECKPOINT, {"messages": messages}
        )
        _append_event_unlocked(logdir, event)
        _compact_events_unlocked(logdir)
    return event


def should_checkpoint(current_seq: int) -> bool:
    """Return True when a checkpoint should be written.

    A checkpoint is due every :py:data:`CHECKPOINT_INTERVAL` events.
    """
    if current_seq == 0:
        return False
    return current_seq % CHECKPOINT_INTERVAL == 0


def find_latest_checkpoint(
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the most recent checkpoint event, or *None*."""
    checkpoint: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == EVENT_CHECKPOINT:
            checkpoint = event
    return checkpoint


def recover_messages(
    logdir: Path,
) -> list[dict[str, Any]] | None:
    """Reconstruct message dicts from the event log.

    Works by:
    1. Finding the latest checkpoint (if any) and loading its message list.
    2. Replaying events after the checkpoint to reconstruct current state.

    Returns *None* if no event log exists.  Returns an empty list if the event
    log exists but all messages have been undone (distinguishable from missing
    log).  Otherwise returns a list of message dicts (in the same format as
    JSONL lines, with ``"timestamp"`` as ISO strings).
    """
    from ..message import _migrate_metadata

    events = read_events(logdir)
    if not events:
        return None

    checkpoint = find_latest_checkpoint(events)
    damaged_seq = _latest_damaged_snapshot_seq(logdir)
    if (
        damaged_seq is not None
        and damaged_seq > (checkpoint["seq"] if checkpoint else 0)
        and not any(e["seq"] < damaged_seq for e in events)
    ):
        # Nothing before it survives (compaction deletes pre-checkpoint rows),
        # so replaying past it would return a list missing what it held.
        raise ValueError(
            f"Event log snapshot at seq {damaged_seq} is damaged; cannot recover"
        )

    messages: list[dict[str, Any]] = []
    start_seq = 0

    if checkpoint:
        # Start from the checkpoint snapshot
        messages.extend(
            _migrate_metadata(dict(msg_dict))  # type: ignore[misc]
            for msg_dict in checkpoint["payload"]["messages"]
        )
        start_seq = checkpoint["seq"]
        logger.info(
            "Recovery: starting from checkpoint at seq %d (%d messages)",
            start_seq,
            len(messages),
        )

    # Replay events after the checkpoint (or from the start)
    replay_count = 0
    for event in events:
        if event["seq"] <= start_seq:
            continue

        event_type = event.get("type")
        if event_type == EVENT_MESSAGE_APPEND:
            msg_dict = _migrate_metadata(dict(event["payload"]["message"]))
            messages.append(msg_dict)  # type: ignore[arg-type]
            replay_count += 1
        elif event_type == EVENT_UNDO:
            n = int(event.get("payload", {}).get("n", 1))
            for _ in range(n):
                if messages:
                    messages.pop()
            replay_count += 1
        elif event_type == EVENT_MESSAGE_EDIT:
            # Edit events store the full message list at time of edit
            messages[:] = [
                _migrate_metadata(dict(m))  # type: ignore[misc]
                for m in event["payload"]["messages"]
            ]
            replay_count += 1

    if replay_count:
        logger.info("Recovery: replayed %d event(s) post-checkpoint", replay_count)

    return messages


# ── convenience: event builders ──────────────────────────────────────


def build_message_append_event(
    seq: int,
    message: Message,
) -> dict[str, Any]:
    """Build a ``message_append`` event from a Message object."""
    return _make_event(seq, EVENT_MESSAGE_APPEND, {"message": message.to_dict()})


def build_message_edit_event(
    seq: int,
    messages: list[Message],
) -> dict[str, Any]:
    """Build a ``message_edit`` event from the current full message list."""
    return _make_event(
        seq,
        EVENT_MESSAGE_EDIT,
        {"messages": [m.to_dict() for m in messages]},
    )


def build_undo_event(seq: int, n: int = 1) -> dict[str, Any]:
    """Build an ``undo`` event storing the count of messages removed."""
    return _make_event(seq, EVENT_UNDO, {"n": n})
