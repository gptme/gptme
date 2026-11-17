"""Adapter layer for converting between gptme and ACP types.

This module provides bidirectional conversion between:
- gptme Message <-> ACP Content
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from ..message import Message

if TYPE_CHECKING:
    from collections.abc import Iterable


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
        text = msg.content
        content.append(
            {
                "type": "text",
                "text": text,
            }
        )

    return content


RoleType = Literal["system", "user", "assistant"]


def acp_content_to_gptme_message(content: list, role: RoleType) -> Message:
    """Convert ACP Content blocks to a gptme Message.

    Handles both dict content blocks and Pydantic model instances
    (e.g. TextContentBlock from the ACP SDK).

    Args:
        content: List of ACP Content blocks (dicts or Pydantic models)
        role: Message role ("system", "user", or "assistant")

    Returns:
        gptme Message instance
    """
    text_parts = []

    for c in content:
        # Support both dict-style access and Pydantic model attribute access
        if isinstance(c, dict):
            if c.get("type") == "text":
                text_parts.append(c.get("text", "") or "")
        else:
            # Pydantic model (e.g. TextContentBlock)
            if getattr(c, "type", None) == "text":
                text_parts.append(getattr(c, "text", "") or "")

    return Message(role=role, content="\n".join(text_parts))


# Schema constructors for outgoing session updates. acp 1.0 removed
# ``acp.helpers`` and its top-level re-exports, so build the schema objects
# directly; this works on both the 0.x and 1.x SDKs.


def text_block(text: str) -> Any:
    from acp.schema import TextContentBlock

    return TextContentBlock(type="text", text=text)


def update_agent_message(content: Any) -> Any:
    from acp.schema import AgentMessageChunk

    return AgentMessageChunk(session_update="agent_message_chunk", content=content)


def update_available_commands(commands: Iterable[Any]) -> Any:
    from acp.schema import AvailableCommandsUpdate

    return AvailableCommandsUpdate(
        session_update="available_commands_update",
        available_commands=list(commands),
    )
