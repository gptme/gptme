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
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..codeblock import Codeblock
from ..logmanager.durability import sync_directory
from ..message import Message
from .base import Parameter, ToolFormat, ToolSpec, ToolUse, _codeblock_char_ranges
from .complete import SessionCompleteException

logger = logging.getLogger(__name__)

CHECKPOINT_FILENAME = "subagent-checkpoint.json"
CHECKPOINT_SCHEMA_VERSION = 1
_COMMITTED_PREFIX = "Checkpoint committed:"
_NOT_COMMITTED_PREFIX = "Checkpoint NOT committed:"


def iter_xml_signal_calls(content: str) -> Generator[ToolUse, None, None]:
    """Parse unfenced XML call groups without modifying their payloads.

    Isolating each group also avoids the shared XML parser's first-occurrence
    offsets confusing an actual call with an identical preceding example.
    Fences inside a real call are answer content, not quoted call groups.
    """
    fences = _codeblock_char_ranges(content)
    groups = re.finditer(
        r"<(tool-use|function_calls)>.*?</\1>", content, flags=re.DOTALL
    )
    for group in groups:
        if any(start <= group.start() < end for start, end in fences):
            continue
        for call in ToolUse.iter_from_content(
            group.group(), tool_format_override="xml"
        ):
            yield replace(call, start=group.start() + (call.start or 0))


def find_yield_block(content: str) -> str | None:
    """Return the last yield payload across child formats, even with yield unloaded.

    Use the shared parsers so fenced examples are not mistaken for calls. Parse
    markdown blocks directly because ToolUse filters those by loaded tools.
    Multiple yields overwrite the checkpoint in execution order: compare against
    the last call, not the first one.
    """
    calls: list[tuple[int, str]] = [
        (block.start or 0, block.content)
        for block in Codeblock.iter_from_markdown(content)
        if block.lang.split() == ["yield"]
    ]
    formats: tuple[ToolFormat, ...] = ("xml", "tool")
    for fmt in formats:
        parsed = (
            iter_xml_signal_calls(content)
            if fmt == "xml"
            else ToolUse.iter_from_content(content, tool_format_override=fmt)
        )
        for call in parsed:
            if call.tool != "yield" or (fmt == "tool" and call.kwargs is None):
                continue
            payload = (
                call.kwargs.get("checkpoint", "")
                if call.kwargs is not None
                else call.content
            )
            calls.append((call.start or 0, payload or ""))
    return sorted(calls, key=lambda item: item[0])[-1][1] if calls else None


def is_commit_receipt(message: Message) -> bool:
    """True for the system message execute_yield emits after a committed write."""
    return message.role == "system" and message.content.startswith(_COMMITTED_PREFIX)


def last_yield_committed(messages: list[Message]) -> bool | None:
    """Return the latest yield receipt's outcome, limited to this assistant turn."""
    for message in reversed(messages):
        if message.role != "system":
            break
        # The last yield result wins, matching checkpoint overwrite order. A
        # rejected later call must let the child repair and retry its payload.
        if message.content.startswith(_NOT_COMMITTED_PREFIX):
            return False
        if is_commit_receipt(message):
            return True
    return None


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
    """Write and sync a checkpoint; failures before replace preserve the old record.

    A namespace-barrier failure after replace cannot roll back the new record,
    but is not acknowledged as a committed yield. Directory-sync portability
    follows the transcript durability policy (no portable Windows barrier).
    """
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
        sync_directory(logdir)
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
    # Markdown blocks deliver the payload as ``code``; native tool calls as kwargs.
    content = code if code is not None else (kwargs or {}).get("checkpoint")
    try:
        payload = parse_checkpoint_payload(content)
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

    # Key on the commit receipt, not the block syntax, so markdown and native
    # tool calls behave alike. Only results of the latest assistant message
    # count: an earlier committed yield in a continued conversation must not
    # stop the new attempt.
    if last_yield_committed(messages):
        logger.info("Yield checkpoint committed — stopping subagent attempt")
        raise SessionCompleteException(
            "Session yielded: checkpoint committed, waiting on external condition"
        )


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
    parameters=[
        Parameter(
            name="checkpoint",
            type="string",
            description=(
                'JSON object: {"wait_for": str, "next_action": str, '
                '"artifacts": [str, ...]}'
            ),
            required=True,
        ),
    ],
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
