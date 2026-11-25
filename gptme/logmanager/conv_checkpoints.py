"""Conversation-level checkpoint primitives for ``/backtrack``.

Lightweight, per-conversation recovery markers that record a message index
(and optional label) so the user can rewind the conversation to a known-good
point after a failed tool run or bad exchange.

This is the *conversation* layer of backtracking (issue #523).  It does NOT
snapshot filesystem state — use workspace checkpoints (``gptme/checkpoint.py``)
for that.

Storage lives in ``<logdir>/conv-checkpoints.jsonl`` alongside the conversation
log.  Records are append-only; the sidecar file is never mutated after writing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ..message import Message


@dataclasses.dataclass(frozen=True)
class ConvCheckpoint:
    """A single conversation checkpoint."""

    index: int  # message count at the time of the checkpoint
    label: str  # user-supplied label or auto-generated
    timestamp: str  # ISO 8601
    prefix_hash: str | None = None  # absent on legacy records and numeric targets


def _sidecar_path(logdir: Path) -> Path:
    return logdir / "conv-checkpoints.jsonl"


def conversation_prefix_hash(messages: Sequence[Message]) -> str:
    """Hash persisted message content, including structured tool calls and files.

    Ignore timestamps: identity is the conversation content, not branch ownership.
    Canonical JSON keeps the digest stable across persistence and reload.
    """
    payload = []
    for message in messages:
        data = message.to_dict()
        data.pop("timestamp", None)
        payload.append(data)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def save_conv_checkpoint(
    logdir: Path, index: int, label: str, *, prefix_hash: str | None = None
) -> ConvCheckpoint:
    """Append a checkpoint to the sidecar.  Returns the new record."""
    record = ConvCheckpoint(
        index=index,
        label=label,
        timestamp=datetime.now(timezone.utc).isoformat(),
        prefix_hash=prefix_hash,
    )
    path = _sidecar_path(logdir)
    line = (json.dumps(dataclasses.asdict(record)) + "\n").encode("utf-8")
    try:
        stream = open(path, "ab+")
    except PermissionError:
        # An appendable sidecar need not be readable. Without tail inspection,
        # a separator is safe for both complete and torn last rows.
        with open(path, "ab") as f:
            f.write(b"\n" + line)
    else:
        with stream as f:
            # Guard against a torn tail with no terminating newline: appending
            # straight after it would glue this record onto the fragment and lose it.
            if f.seek(0, 2) > 0:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    line = b"\n" + line
            f.write(line)
    return record


def list_conv_checkpoints(logdir: Path) -> list[ConvCheckpoint]:
    """Return all checkpoints for this conversation, oldest first."""
    path = _sidecar_path(logdir)
    if not path.exists():
        return []
    records: list[ConvCheckpoint] = []
    with open(path, "rb") as f:
        for raw_line in f:
            # Decode per line so one damaged byte skips only its own record.
            try:
                line = raw_line.decode("utf-8").strip()
                data = json.loads(line) if line else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            index = data.get("index")
            label = data.get("label")
            timestamp = data.get("timestamp")
            prefix_hash = data.get("prefix_hash")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not isinstance(label, str)
                or not isinstance(timestamp, str)
                or not (prefix_hash is None or isinstance(prefix_hash, str))
            ):
                continue
            records.append(
                ConvCheckpoint(
                    index=index,
                    label=label,
                    timestamp=timestamp,
                    prefix_hash=prefix_hash,
                )
            )
    return records


def is_message_index(identifier: str) -> bool:
    """Whether a checkpoint identifier is a bare message index, not a label.

    ``isdigit()`` accepts non-ASCII digits like ² and ३, so restrict to [0-9],
    and allow at most one leading minus: "--5" is a label (int() rejects it).
    """
    return re.fullmatch(r"-?[0-9]+", identifier) is not None


def resolve_conv_checkpoint(
    logdir: Path,
    identifier: str,
) -> ConvCheckpoint:
    """Resolve a label or bare integer to a checkpoint.

    Priority:
    1. Pure decimal integer → treat as **message index** (not checkpoint #).
    2. Otherwise → match against checkpoint labels (most-recent match wins).
    """
    records = list_conv_checkpoints(logdir)

    if is_message_index(identifier):
        msg_idx = int(identifier)
        return ConvCheckpoint(
            index=msg_idx,
            label=f"@{msg_idx}",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # Label match (last one wins so the most-recent label takes precedence)
    matches = [r for r in records if r.label == identifier]
    if matches:
        return matches[-1]

    raise KeyError(
        f"no checkpoint named {identifier!r} found "
        f"(use a label, message index, or /backtrack list)"
    )
