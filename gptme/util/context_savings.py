"""Per-conversation telemetry for context saved by truncating large tool outputs."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

CONTEXT_SAVINGS_FILENAME = "context-savings.jsonl"


@dataclass
class ContextSavingsSummary:
    # Not frozen: the dict fields would still be mutable under frozen=True,
    # which makes the annotation misleading. Callers should treat instances as
    # read-only by convention.
    entries: int = 0
    total_saved_tokens: int = 0
    max_saved_tokens: int = 0
    saved_tokens_by_source: dict[str, int] = field(default_factory=dict)
    calls_by_source: dict[str, int] = field(default_factory=dict)


def _ledger_path(logdir: Path) -> Path:
    return logdir / CONTEXT_SAVINGS_FILENAME


def record_context_savings(
    logdir: Path,
    source: str,
    original_tokens: int,
    kept_tokens: int,
    command_info: str | None = None,
    saved_path: Path | None = None,
) -> None:
    """Append one truncation-savings record for the current conversation."""
    saved_tokens = original_tokens - kept_tokens
    if saved_tokens <= 0:
        return

    ledger_path = _ledger_path(logdir)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "original_tokens": original_tokens,
        "kept_tokens": kept_tokens,
        "saved_tokens": saved_tokens,
    }
    if command_info:
        payload["command_info"] = command_info
    if saved_path:
        payload["saved_path"] = str(saved_path)

    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def summarize_context_savings(logdir: Path) -> ContextSavingsSummary:
    """Aggregate truncation-savings records for a conversation."""
    ledger_path = _ledger_path(logdir)
    if not ledger_path.exists():
        return ContextSavingsSummary()

    saved_tokens_by_source: defaultdict[str, int] = defaultdict(int)
    calls_by_source: defaultdict[str, int] = defaultdict(int)
    entries = 0
    total_saved_tokens = 0
    max_saved_tokens = 0

    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed context-savings row: %r", line[:200])
            continue

        saved_tokens = int(row.get("saved_tokens") or 0)
        source = str(row.get("source") or "unknown")
        entries += 1
        total_saved_tokens += saved_tokens
        max_saved_tokens = max(max_saved_tokens, saved_tokens)
        saved_tokens_by_source[source] += saved_tokens
        calls_by_source[source] += 1

    return ContextSavingsSummary(
        entries=entries,
        total_saved_tokens=total_saved_tokens,
        max_saved_tokens=max_saved_tokens,
        saved_tokens_by_source=dict(saved_tokens_by_source),
        calls_by_source=dict(calls_by_source),
    )
