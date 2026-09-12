"""Append-only observability for explicit context compaction events."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from os import PathLike
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EVENT_LOG_NAME = "compaction.jsonl"
_event_locks_guard = threading.Lock()
_event_locks: dict[Path, threading.Lock] = {}


def _event_lock(logdir: Path) -> threading.Lock:
    path = (logdir / EVENT_LOG_NAME).resolve()
    with _event_locks_guard:
        lock = _event_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _event_locks[path] = lock
        return lock


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
    path = logdir / EVENT_LOG_NAME
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line in file:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed compaction event in %s", path)
    return events
