"""
Master Context utilities for tracking byte offsets in conversation.jsonl.

The master context is the original conversation.jsonl file which is never
compacted. This allows aggressive compaction on the working context while
preserving exact recovery via byte ranges.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..message import Message

logger = logging.getLogger(__name__)


def is_tool_result_message(messages: Sequence[Message], index: int) -> bool:
    """Return whether ``messages[index]`` is a persisted tool result.

    Native tool results have a ``call_id``. Markdown and XML tool formats do
    not, so their result is identified by the runnable assistant tool call
    immediately before it. This mirrors the compaction engine's pairing rule.
    """
    if index < 0 or index >= len(messages):
        return False
    message = messages[index]
    if message.role != "system":
        return False
    # Status/UI messages (hook notices, compaction status, UI-only messages)
    # are never tool results, and never outputs of a call.
    if message.hide or message.quiet or message.ui_only:
        return False
    if message.call_id:
        return True
    if index == 0:
        return False

    from .reduce import message_contains_tool_use

    # Markdown and XML tool calls can emit several provider-visible output
    # messages (e.g. a non-blocking warning before the final result). Look
    # back past system messages and flagged status messages to the nearest
    # provider-visible non-system message; every system output in that run
    # belongs to the call.
    lookback = index - 1
    while lookback >= 0 and (
        messages[lookback].role == "system" or _is_status_message(messages[lookback])
    ):
        lookback -= 1
    return lookback >= 0 and message_contains_tool_use(messages[lookback])


def _is_status_message(message: Message) -> bool:
    return bool(message.hide or message.quiet or message.ui_only)


@dataclass
class MessageByteRange:
    """Byte range of a message in the master log."""

    message_idx: int
    byte_start: int
    byte_end: int


def build_master_context_index(logfile: Path) -> list[MessageByteRange]:
    """
    Build an index of byte offsets for each message in the master log.

    Args:
        logfile: Path to conversation.jsonl (master log)

    Returns:
        List of MessageByteRange objects mapping message indices to byte ranges
    """
    index = []

    try:
        with open(logfile, "rb") as f:
            byte_offset = 0
            msg_idx = 0

            for line in f:
                line_len = len(line)
                index.append(
                    MessageByteRange(
                        message_idx=msg_idx,
                        byte_start=byte_offset,
                        byte_end=byte_offset + line_len,
                    )
                )
                byte_offset += line_len
                msg_idx += 1

    except FileNotFoundError:
        logger.warning(f"Master log not found: {logfile}")
        return []

    return index


def create_master_context_reference(
    logfile: Path,
    byte_range: MessageByteRange,
    original_tokens: int,
    preview: str | None = None,
) -> str:
    """
    Create a truncation reference pointing to the master context.

    Args:
        logfile: Path to conversation.jsonl
        byte_range: Byte range of the truncated message
        original_tokens: Number of tokens in original content
        preview: Optional short preview of content (first few lines)

    Returns:
        Reference string with byte range for recovery
    """
    parts = [
        f"[Content truncated - {original_tokens} tokens]",
        f"Master context: {logfile} (bytes {byte_range.byte_start}-{byte_range.byte_end})",
    ]

    if preview:
        # Limit preview to first 200 chars, add ellipsis only if truncated
        parts.append(f"Preview: {preview[:200]}{'...' if len(preview) > 200 else ''}")

    parts.append(
        "To recover: grep or read the master context file at the byte range above."
    )

    return "\n".join(parts)


def recover_from_master_context(logfile: Path, byte_range: MessageByteRange) -> str:
    """
    Recover truncated content from the master context.

    Args:
        logfile: Path to conversation.jsonl
        byte_range: Byte range to read

    Returns:
        Original content from master log

    Raises:
        FileNotFoundError: If master log doesn't exist
        ValueError: If byte range contains invalid JSON or missing content
    """
    try:
        with open(logfile, "rb") as f:
            f.seek(byte_range.byte_start)
            data = f.read(byte_range.byte_end - byte_range.byte_start)
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Master log not found: {logfile}") from e

    # Parse the JSON line to get the message content
    # Use errors="replace" to handle invalid UTF-8 sequences gracefully
    try:
        json_data = json.loads(data.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Invalid JSON at byte range {byte_range.byte_start}-{byte_range.byte_end}: {e}"
        ) from e

    if "content" not in json_data:
        raise ValueError(
            f"Message at byte range {byte_range.byte_start}-{byte_range.byte_end} "
            f"has no 'content' field"
        )
    return json_data["content"]
