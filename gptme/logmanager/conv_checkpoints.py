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
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclasses.dataclass(frozen=True)
class ConvCheckpoint:
    """A single conversation checkpoint."""

    index: int  # message count at the time of the checkpoint
    label: str  # user-supplied label or auto-generated
    timestamp: str  # ISO 8601


def _sidecar_path(logdir: Path) -> Path:
    return logdir / "conv-checkpoints.jsonl"


def save_conv_checkpoint(logdir: Path, index: int, label: str) -> ConvCheckpoint:
    """Append a checkpoint to the sidecar.  Returns the new record."""
    record = ConvCheckpoint(
        index=index,
        label=label,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    path = _sidecar_path(logdir)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dataclasses.asdict(record)) + "\n")
    return record


def list_conv_checkpoints(logdir: Path) -> list[ConvCheckpoint]:
    """Return all checkpoints for this conversation, oldest first."""
    path = _sidecar_path(logdir)
    if not path.exists():
        return []
    records: list[ConvCheckpoint] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(
                    ConvCheckpoint(
                        index=data["index"],
                        label=data["label"],
                        timestamp=data["timestamp"],
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue
    return records


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

    if identifier.lstrip("-").isdigit():
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
