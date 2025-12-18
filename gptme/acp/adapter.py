"""Adapter layer for converting between gptme and ACP types.

This module provides bidirectional conversion between:
- gptme Message <-> ACP Content
- gptme Codeblock <-> ACP ToolCall
- gptme LogManager <-> ACP Session
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal

from ..message import Message

if TYPE_CHECKING:
    from ..codeblock import Codeblock


def generate_id() -> str:
    """Generate a unique ID for ACP objects."""
    return str(uuid.uuid4())[:8]


def gptme_message_to_acp_content(msg: Message) -> list[dict]:
    """Convert a gptme Message to ACP Content blocks.

    Args:
        msg: gptme Message to convert

    Returns:
        List of ACP Content dictionaries (TextContent format)
    """
    content = []

    # Add main text content
    if msg.content:
        # TODO: Handle codeblocks in Phase 2 - for now, just append plain text
        text = msg.content
        content.append(
            {
                "type": "text",
                "text": text,
            }
        )

    return content


RoleType = Literal["system", "user", "assistant"]


def acp_content_to_gptme_message(content: list[dict], role: RoleType) -> Message:
    """Convert ACP Content blocks to a gptme Message.

    Args:
        content: List of ACP Content dictionaries
        role: Message role ("system", "user", or "assistant")

    Returns:
        gptme Message instance
    """
    text_parts = []

    for c in content:
        if c.get("type") == "text":
            text_parts.append(c.get("text", ""))

    return Message(role=role, content="\n".join(text_parts))


def gptme_codeblock_to_tool_info(block: Codeblock) -> dict:
    """Convert a gptme Codeblock to ACP tool information.

    Args:
        block: gptme Codeblock

    Returns:
        Dictionary with tool information for ACP
    """
    # Determine tool kind based on language
    kind = "execute"
    if block.lang in ("save", "append", "patch"):
        kind = "edit"
    elif block.lang == "shell":
        kind = "terminal"

    return {
        "id": generate_id(),
        "kind": kind,
        "name": f"{block.lang} execution",
        "language": block.lang,
        "content": block.content,
    }


def format_tool_result(result: str | None, success: bool = True) -> dict:
    """Format tool execution result for ACP.

    Args:
        result: Tool execution output
        success: Whether execution succeeded

    Returns:
        ACP-formatted result dictionary
    """
    return {
        "status": "completed" if success else "failed",
        "output": result or "",
    }
