"""Yield tool — a subagent records a durable checkpoint and ends its attempt.

A child that has done everything it can before an *external* condition is met
(CI, a review, a deploy) should not hold a worker slot while it waits, and
should not pretend it is done. ``yield`` writes an atomic checkpoint into the
conversation directory and stops the session; the parent reports the attempt
as ``blocked_external`` and may later resume the same conversation with
``subagent_continue()`` once it has verified the condition itself.

The checkpoint is data, never a command: core does not fetch, poll or evaluate
the wait condition. A checkpoint that fails to commit is reported back to the
child and the session keeps running, so a failed write can never surface as a
resumable yield.

Only enabled in subagent sessions (disabled_by_default=True).
"""

import json
import logging
import math
import os
import re
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..message import Message
from .base import ToolSpec
from .complete import SessionCompleteException

logger = logging.getLogger(__name__)

CHECKPOINT_FILENAME = "subagent-checkpoint.json"
CHECKPOINT_SCHEMA_VERSION = 1
_COMMITTED_PREFIX = "Checkpoint committed:"
_NOT_COMMITTED_PREFIX = "Checkpoint NOT committed:"


_YIELD_BLOCK_RE = re.compile(r"```yield\s*\n(.*?)\n```", re.DOTALL)


def find_yield_block(content: str) -> str | None:
    """Return a yield block's payload, independent of which tools are loaded."""
    match = _YIELD_BLOCK_RE.search(content)
    return match.group(1) if match else None


class CheckpointError(ValueError):
    """The yield payload is invalid or the checkpoint could not be written."""


def parse_checkpoint_payload(content: str | None) -> dict[str, Any]:
    """Validate a yield block's JSON payload and return its normalized fields."""
    try:
        data = json.loads(content or "")
    except json.JSONDecodeError as e:
        raise CheckpointError(f"payload is not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise CheckpointError("payload must be a JSON object")
    return _validate_payload(data)


def _validate_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("wait_for", "next_action"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise CheckpointError(f"'{key}' must be a non-empty string")
        payload[key] = value.strip()
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list) or not all(
        isinstance(item, str) and item.strip() for item in artifacts
    ):
        raise CheckpointError("'artifacts' must be a list of non-empty strings")
    payload["artifacts"] = [item.strip() for item in artifacts]
    return payload


def write_checkpoint(logdir: Path, payload: dict[str, Any]) -> Path:
    """Atomically write the checkpoint record; the old record survives a failure."""
    record = {
        "schema": CHECKPOINT_SCHEMA_VERSION,
        "conversation": logdir.name,
        "created_at": time.time(),
        **payload,
    }
    path = logdir / CHECKPOINT_FILENAME
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{CHECKPOINT_FILENAME}.", suffix=".tmp", dir=logdir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return path


def load_checkpoint(logdir: Path) -> dict[str, Any] | None:
    """Return the committed checkpoint record, or None if absent or malformed."""
    try:
        record = json.loads((logdir / CHECKPOINT_FILENAME).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("schema") != (
        CHECKPOINT_SCHEMA_VERSION
    ):
        return None
    created_at = record.get("created_at")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, int | float)
        or not math.isfinite(created_at)
    ):
        return None
    try:
        payload = _validate_payload(record)
    except CheckpointError:
        return None
    return {**record, **payload}


def _current_logdir() -> Path | None:
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    return manager.logdir if manager and manager.logdir else None


def execute_yield(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Message:
    """Commit the checkpoint; the session stops only if this succeeded."""
    try:
        payload = parse_checkpoint_payload(code)
        logdir = _current_logdir()
        if logdir is None:
            raise CheckpointError("no conversation directory for this session")
        try:
            path = write_checkpoint(logdir, payload)
        except OSError as e:
            raise CheckpointError(f"write failed ({e})") from None
    except CheckpointError as e:
        return Message(
            "system",
            f"{_NOT_COMMITTED_PREFIX} {e}. The attempt has NOT yielded. Fix the "
            "payload and retry `yield`, or use `complete`/`clarify` instead.",
        )
    return Message(
        "system",
        f"{_COMMITTED_PREFIX} {path}. Yielding this attempt as blocked_external.",
    )


def yield_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """Stop the session after a committed yield, before the next generation."""
    if False:
        yield

    # Only the latest assistant message and its tool results count: an earlier
    # committed yield in a continued conversation must not stop the new attempt.
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user":
            return
        if message.role != "assistant":
            continue
        if find_yield_block(message.content) is None:
            return
        if any(
            later.role == "system" and later.content.startswith(_COMMITTED_PREFIX)
            for later in messages[index + 1 :]
        ):
            logger.info("Yield checkpoint committed — stopping subagent attempt")
            raise SessionCompleteException(
                "Session yielded: checkpoint committed, waiting on external condition"
            )
        return


tool = ToolSpec(
    name="yield",
    desc="Checkpoint and end this attempt while an external condition is pending",
    disabled_by_default=True,
    instructions="""
Use this block when your remaining work depends on something outside your
control (CI, a review, a deploy) and nothing more can be done until it happens.
Write a JSON object with what you are waiting for, what to do once it is met,
and the artifacts you already produced (branch, commit, PR, file paths):

```yield
{"wait_for": "CI green on PR #123", "next_action": "merge PR #123 and update the changelog", "artifacts": ["PR #123", "branch feat/x"]}
```

The checkpoint is saved and this attempt ends as ``blocked_external``. The
parent checks the condition and continues this same conversation later, so do
not redo work recorded in ``artifacts``. If the checkpoint is not committed you
will be told why and must not assume you have yielded. Use ``complete`` when
done and ``clarify`` when you need an answer from the parent.
""".strip(),
    block_types=["yield"],
    execute=execute_yield,
    available=True,
    hooks={
        "yield": (
            "generation.pre",  # HookType.GENERATION_PRE.value
            yield_hook,
            998,  # Below complete (1000) and clarify (999)
        )
    },
)
