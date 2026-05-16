"""Shared helpers for OpenAI Responses API providers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from typing_extensions import NotRequired

from .utils import extract_tool_uses_from_assistant_message, parameters2dict

if TYPE_CHECKING:
    from ..message import Message
    from ..tools import ToolSpec

logger = logging.getLogger(__name__)


class ContentPart(TypedDict):
    """A content part in a multimodal message."""

    type: str
    text: NotRequired[str]
    image_url: NotRequired[dict[str, str]]


MessageContent = str | list[ContentPart | str] | None


class ToolCallFunction(TypedDict):
    """Function details in a tool call."""

    name: str
    arguments: str


class ToolCall(TypedDict):
    """A tool call in an assistant message."""

    id: str
    type: str
    function: ToolCallFunction


class MessageDict(TypedDict):
    """Dictionary representation of a chat message for API calls."""

    role: str
    content: MessageContent
    tool_calls: NotRequired[list[ToolCall]]
    tool_call_id: NotRequired[str]
    call_id: NotRequired[str]
    reasoning_content: NotRequired[str]
    files: NotRequired[list[str]]


@dataclass(frozen=True)
class UsageTokenCounts:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    total_tokens: int | None


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _longest_suffix_prefix(text: str, pattern: str) -> str:
    """Return the longest suffix of ``text`` that could continue ``pattern``."""
    max_len = min(len(text), len(pattern) - 1)
    for size in range(max_len, 0, -1):
        suffix = text[-size:]
        if pattern.startswith(suffix):
            return suffix
    return ""


def _filter_duplicate_thinking_text(
    text: str, *, in_thinking_block: bool
) -> tuple[str, str, bool]:
    """Strip raw <thinking>...</thinking> spans after structured reasoning events."""

    open_tag = "<thinking>"
    close_tag = "</thinking>"
    output_parts: list[str] = []
    remaining = text

    while remaining:
        if in_thinking_block:
            close_index = remaining.find(close_tag)
            if close_index == -1:
                pending = _longest_suffix_prefix(remaining, close_tag)
                return "".join(output_parts), pending, True
            remaining = remaining[close_index + len(close_tag) :]
            in_thinking_block = False
            continue

        open_index = remaining.find(open_tag)
        if open_index == -1:
            pending = _longest_suffix_prefix(remaining, open_tag)
            if pending:
                output_parts.append(remaining[: -len(pending)])
            else:
                output_parts.append(remaining)
            return "".join(output_parts), pending, False

        output_parts.append(remaining[:open_index])
        remaining = remaining[open_index + len(open_tag) :]
        in_thinking_block = True

    return "".join(output_parts), "", in_thinking_block


def _content_to_text(content: MessageContent) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    text_parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            text_parts.append(part.get("text", ""))
    return "".join(text_parts)


def _content_to_responses_input(content: MessageContent) -> str | list[dict[str, Any]]:
    if content is None:
        return ""
    if isinstance(content, str):
        return content

    converted: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            converted.append({"type": "input_text", "text": part})
            continue
        if not isinstance(part, dict):
            raise TypeError(f"Unsupported content part type: {type(part)!r}")

        part_type = part.get("type")
        if part_type == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
        elif part_type == "image_url":
            image_url = _obj_get(part.get("image_url", {}), "url")
            if not image_url:
                raise ValueError("image_url content part missing url")
            converted.append(
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": "auto",
                }
            )
        else:
            raise ValueError(f"Unsupported responses input part type: {part_type!r}")

    if all(item["type"] == "input_text" for item in converted):
        return "".join(item["text"] for item in converted)
    return converted


def _tool_spec_to_responses_tool(spec: ToolSpec) -> dict[str, Any]:
    """Convert a ToolSpec to the flat Responses API function tool format."""
    name = spec.block_types[0] if spec.block_types else spec.name
    description = spec.get_instructions("tool") or spec.desc or ""
    if len(description) > 1024:
        logger.warning(
            "Description for tool `%s` is too long ( %d > 1024 chars). Truncating...",
            spec.name,
            len(description),
        )
        description = description[:1024]
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters2dict(spec.parameters),
    }


def _messages_dicts_to_responses_input(
    messages_dicts: list[MessageDict],
) -> tuple[str | None, list[dict[str, Any]]]:
    instructions_parts: list[str] = []
    items: list[dict[str, Any]] = []

    for message in messages_dicts:
        role = message["role"]
        tool_call_id = _obj_get(message, "tool_call_id") or _obj_get(message, "call_id")

        if role == "system" and tool_call_id:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": _content_to_text(message["content"]),
                }
            )
            continue

        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": _content_to_text(message["content"]),
                }
            )
            continue

        if role == "system":
            text = _content_to_text(message["content"]).strip()
            if text:
                instructions_parts.append(text)
            continue

        if role == "assistant" and message.get("tool_calls"):
            text = _content_to_text(message.get("content")).strip()
            if text:
                items.append({"role": "assistant", "content": text})
            for tool_call in message["tool_calls"]:
                function = tool_call["function"]
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call["id"],
                        "name": function["name"],
                        "arguments": function["arguments"],
                    }
                )
            continue

        items.append(
            {
                "role": role,
                "content": _content_to_responses_input(message["content"]),
            }
        )

    instructions = "\n\n".join(instructions_parts).strip() or None
    return instructions, items


def _messages_to_responses_input(
    messages: list[Message],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert gptme messages to Responses API instructions and input items."""
    instructions_parts: list[str] = []
    items: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system" and msg.call_id:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.call_id,
                    "output": msg.content,
                }
            )
            continue

        if msg.role == "system":
            text = _content_to_text(msg.content).strip()
            if text:
                instructions_parts.append(text)
            continue

        if msg.role == "assistant":
            content_parts, tool_uses = extract_tool_uses_from_assistant_message(
                msg.content, tool_format_override="tool"
            )
            text = "".join(
                part["text"] if isinstance(part, dict) else str(part)
                for part in content_parts
            ).strip()
            if text:
                items.append({"role": "assistant", "content": text})
            elif not tool_uses:
                items.append({"role": "assistant", "content": msg.content})
            items.extend(
                {
                    "type": "function_call",
                    "name": tooluse.tool,
                    "call_id": tooluse.call_id or "",
                    "arguments": json.dumps(tooluse.kwargs or {}),
                }
                for tooluse in tool_uses
            )
            continue

        items.append(
            {
                "role": msg.role,
                "content": _content_to_responses_input(msg.content),
            }
        )

    instructions = "\n\n".join(instructions_parts).strip() or None
    return instructions, items


def _extract_usage_token_counts(usage: Any) -> UsageTokenCounts:
    """Normalize Chat Completions and Responses API usage token fields."""
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    details = getattr(usage, "prompt_tokens_details", None)
    if prompt_tokens is None:
        prompt_tokens = getattr(usage, "input_tokens", None)
        details = getattr(usage, "input_tokens_details", None)
    if output_tokens is None:
        output_tokens = getattr(usage, "output_tokens", None)
    cache_read_tokens = getattr(details, "cached_tokens", None)
    cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", None)
    if cache_creation_tokens is None:
        cache_creation_tokens = getattr(details, "cache_write_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    if isinstance(prompt_tokens, int):
        cache_read = cache_read_tokens if isinstance(cache_read_tokens, int) else 0
        cache_create = (
            cache_creation_tokens if isinstance(cache_creation_tokens, int) else 0
        )
        input_tokens = prompt_tokens - cache_read - cache_create
    else:
        input_tokens = None

    return UsageTokenCounts(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        total_tokens=total_tokens,
    )
