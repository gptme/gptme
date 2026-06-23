"""Append-only event log for session durability.

Provides an append-only JSONL event log alongside the primary
``conversation.jsonl``.  Periodic checkpoint cells (every
:py:data:`CHECKPOINT_INTERVAL` events) allow efficient recovery of the
message list from the event log alone — skipping replay from event 1.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from ..message import Message

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


def append_event(logdir: Path, event: dict[str, Any]) -> None:
    """Append a single event record to the event log."""
    path = _event_log_path(logdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def read_events(logdir: Path) -> list[dict[str, Any]]:
    """Read all events from the event log, oldest first.

    Returns an empty list if no event log exists.
    """
    path = _event_log_path(logdir)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping malformed event line in %s", path)
    return events


def sequence_number(logdir: Path) -> int:
    """Return the next sequence number for a new event."""
    path = _event_log_path(logdir)
    if not path.exists():
        return 1
    # Read only the last non-empty line to avoid O(n) full-file parse
    last_line = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if not last_line:
        return 1
    try:
        return json.loads(last_line)["seq"] + 1
    except (json.JSONDecodeError, KeyError):
        return len(read_events(logdir)) + 1


def write_checkpoint(
    logdir: Path,
    seq: int,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write a checkpoint event that snapshots the full message state.

    Returns the written checkpoint event.
    """
    event = _make_event(seq, EVENT_CHECKPOINT, {"messages": messages})
    append_event(logdir, event)
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
