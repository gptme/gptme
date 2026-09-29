"""Append-only observability for explicit context compaction events."""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
import weakref
from contextlib import contextmanager
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

EVENT_LOG_NAME = "compaction.jsonl"
SHADOW_LOG_NAME = "phase0-shadow.jsonl"
# The shadow ledger records per-message decisions on every compaction. Cap its
# on-disk growth and rotate one generation; readers use the current file.
SHADOW_LOG_MAX_BYTES = 5 * 1024 * 1024
_event_locks_guard = threading.Lock()
_event_locks: weakref.WeakValueDictionary[Path, threading.Lock] = (
    weakref.WeakValueDictionary()
)

try:
    fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:
    msvcrt: Any = importlib.import_module("msvcrt")
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


def _event_thread_lock(path: Path) -> threading.Lock:
    key = path.resolve()
    with _event_locks_guard:
        lock = _event_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _event_locks[key] = lock
        return lock


@contextmanager
def _event_lock(logdir: Path, log_name: str = EVENT_LOG_NAME) -> Iterator[None]:
    """Serialize event appends across threads and processes."""
    path = logdir / log_name
    lock_path = logdir / f".{log_name}.lock"
    thread_lock = _event_thread_lock(path)
    with thread_lock, lock_path.open("a+b") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        elif msvcrt is not None:  # pragma: no cover - Windows
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


def append_compaction_event(
    logdir: str | PathLike[str] | None,
    *,
    trigger: str,
    method: str,
    tokens_before: int,
    tokens_after: int,
    messages_before: int,
    messages_after: int,
    elapsed_seconds: float,
    retry_success: bool | None = None,
    provider_tokens_before: int | None = None,
    provider_tokens_after: int | None = None,
    checkpoint_tokens: int | None = None,
    files_reloaded: list[str] | None = None,
    cache_read_tokens_next: int | None = None,
    cache_write_tokens_next: int | None = None,
) -> dict[str, Any]:
    """Append one compaction event and return the written record.

    Logging is best-effort: inability to write observability must never prevent
    recovery from an already-failing provider request.
    """
    saved = tokens_before - tokens_after
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "method": method,
        "tokens": {
            "before_estimated": tokens_before,
            "after_estimated": tokens_after,
        },
        "messages": {"before": messages_before, "after": messages_after},
        "savings": {
            "tokens": saved,
            "ratio": saved / tokens_before if tokens_before else 0.0,
        },
        "elapsed_seconds": elapsed_seconds,
    }
    optional = {
        "retry_success": retry_success,
        "provider_tokens_before": provider_tokens_before,
        "provider_tokens_after": provider_tokens_after,
        "checkpoint_tokens": checkpoint_tokens,
        "files_reloaded": files_reloaded,
        "cache_read_tokens_next": cache_read_tokens_next,
        "cache_write_tokens_next": cache_write_tokens_next,
    }
    event.update({key: value for key, value in optional.items() if value is not None})

    if logdir is None or not isinstance(logdir, (str, PathLike)):
        return event
    logdir = Path(logdir)
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        with (
            _event_lock(logdir),
            (logdir / EVENT_LOG_NAME).open("a", encoding="utf-8") as file,
        ):
            file.write(json.dumps(event, separators=(",", ":")) + "\n")
    except OSError as exc:
        logger.warning("Failed to append compaction event: %s", exc)
    return event


def read_compaction_events(logdir: Path) -> list[dict[str, Any]]:
    """Read valid events from ``compaction.jsonl`` in append order."""
    return _read_jsonl_events(logdir / EVENT_LOG_NAME)


def append_phase0_shadow_event(
    logdir: str | PathLike[str] | None,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Append one Phase-0 shadow ledger record and return it.

    Stored in ``phase0-shadow.jsonl`` (not ``compaction.jsonl``) so existing
    compaction-event consumers keep seeing only actual compact operations.

    Logging is best-effort: evaluation must never block compaction.
    """
    n_drop = sum(1 for d in decisions if d.get("decision") == "drop")
    tokens_saved = sum(int(d.get("tokens_saved") or 0) for d in decisions)
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trigger": "phase0-shadow",
        "method": "heuristic",
        "n_candidates": len(decisions),
        "n_drop": n_drop,
        "n_keep": len(decisions) - n_drop,
        "tokens_saved": tokens_saved,
        "decisions": decisions,
    }

    if logdir is None or not isinstance(logdir, (str, PathLike)):
        return event
    logdir = Path(logdir)
    log_path = logdir / SHADOW_LOG_NAME
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        with _event_lock(logdir, SHADOW_LOG_NAME):
            _rotate_if_oversized(log_path, SHADOW_LOG_MAX_BYTES)
            with log_path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(event, separators=(",", ":")) + "\n")
    except OSError as exc:
        logger.warning("Failed to append Phase-0 shadow event: %s", exc)
    return event


def _rotate_if_oversized(path: Path, max_bytes: int) -> None:
    """Move ``path`` to ``<path>.1`` once it exceeds ``max_bytes``.

    Best-effort: a rotation failure must not stop the append, which still
    needs to record the evaluation sample.
    """
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            path.replace(path.with_name(path.name + ".1"))
    except OSError as exc:
        logger.warning("Failed to rotate %s: %s", path, exc)


def read_phase0_shadow_events(logdir: Path) -> list[dict[str, Any]]:
    """Read valid Phase-0 shadow records from the retained and current ledgers.

    Rotation moves older records to ``phase0-shadow.jsonl.1``; evaluation must
    still see them, so the retained generation is read first (append order).
    Both generations are read under the rotation lock: a rotation between the
    two reads can otherwise move the current records into ``.1`` after it was
    read, silently dropping them from evaluation.
    """
    logdir = Path(logdir)
    if not logdir.exists():
        return []
    try:
        with _event_lock(logdir, SHADOW_LOG_NAME):
            return _read_jsonl_events(
                logdir / f"{SHADOW_LOG_NAME}.1"
            ) + _read_jsonl_events(logdir / SHADOW_LOG_NAME)
    except OSError as exc:
        logger.warning("Failed to lock Phase-0 shadow ledger for reading: %s", exc)
        return _read_jsonl_events(logdir / f"{SHADOW_LOG_NAME}.1") + _read_jsonl_events(
            logdir / SHADOW_LOG_NAME
        )


def _read_jsonl_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed event in %s", path)
    return events
