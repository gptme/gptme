"""Shared helpers for OpenAI Responses API providers."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

import httpx
from typing_extensions import NotRequired

from ..tools.base import truncate_tool_description
from ..tools.native_names import canonical_tool_name, native_tool_name
from .utils import extract_tool_uses_from_assistant_message, parameters2dict

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

    from ..message import Message
    from ..tools import ToolSpec

logger = logging.getLogger(__name__)

# Responses API rejects `instructions` longer than 1,048,576 characters.
_RESPONSES_INSTRUCTIONS_MAX_CHARS = 1_048_576


class ResponsesStreamError(httpx.RemoteProtocolError):
    """Explicit failure reported inside an HTTP-200 Responses stream."""

    def __init__(self, event_type: str, code: str, message: str):
        self.event_type = event_type
        self.code = code
        self.message = message
        self.body = {"code": code, "message": message}
        super().__init__(f"Responses stream {event_type}: {code}: {message}")


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
    # Only the Responses preparation path populates these validated items.
    responses_items: NotRequired[list[dict[str, Any]]]


@dataclass(frozen=True)
class UsageTokenCounts:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    total_tokens: int | None
    reasoning_tokens: int | None = None


def _obj_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def served_model_from(obj: Any) -> str | None:
    """Return ``obj.model`` (dict key or attribute) if it is a non-empty string.

    Used to record the model id the provider reports in its response as
    ``MessageMetadata.served_model``. Anything else (missing, ``None``, empty,
    or a non-string such as a test mock) yields ``None`` so the key stays absent.
    """
    if obj is None:
        return None
    value = _obj_get(obj, "model", None)
    if isinstance(value, str) and value.strip():
        return value
    return None


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
    name = native_tool_name(spec.name)
    # Prefer a format-specific compact summary verbatim (see _spec2tool).
    description = (
        spec.instructions_format.get("tool")
        or spec.get_instructions("tool")
        or spec.desc
        or ""
    )
    description = truncate_tool_description(description, spec.name)
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters2dict(spec.parameters),
    }


def _pair_missing_tool_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep interrupted or unexecuted tool calls replayable without inventing results.

    A result belongs to the nearest preceding unpaired call with its ID, so a
    reused ID can neither hide a later orphaned call nor steal an earlier
    call's result.
    """
    pending: dict[str, list[int]] = {}
    matched_output_indices: set[int] = set()
    for idx, item in enumerate(items):
        if item.get("type") == "function_call":
            pending.setdefault(item["call_id"], []).append(idx)
        elif item.get("type") == "function_call_output":
            calls = pending.get(item["call_id"])
            if calls:
                calls.pop()
                matched_output_indices.add(idx)
    orphan_calls = {idx for calls in pending.values() for idx in calls}

    paired_items: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if (
            item.get("type") == "function_call_output"
            and idx not in matched_output_indices
        ):
            # Drop orphaned results — no preceding unpaired call was recorded.
            # Sending an unmatched result to the Responses API causes a 400 error.
            logger.warning(
                "Dropping orphaned tool result for call_id %s (no preceding call recorded)",
                item["call_id"],
            )
            continue
        paired_items.append(item)
        if idx in orphan_calls:
            logger.warning("No tool result recorded for call_id %s", item["call_id"])
            paired_items.append(
                {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": "Error: No tool result was recorded for this call. "
                    "Execution may have been skipped or interrupted; do not assume it succeeded.",
                }
            )
    return paired_items


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

        if role == "assistant" and "responses_items" in message:
            items.extend(message["responses_items"])
            continue

        if role == "assistant" and message.get("tool_calls"):
            # Ordinary fallback must retain processed image parts alongside calls.
            content = _content_to_responses_input(message.get("content"))
            if isinstance(content, str):
                content = content.strip()
            if content:
                items.append({"role": "assistant", "content": content})
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
    if instructions and len(instructions) > _RESPONSES_INSTRUCTIONS_MAX_CHARS:
        logger.warning(
            "Truncating instructions to %d chars (Responses API cap)",
            _RESPONSES_INSTRUCTIONS_MAX_CHARS,
        )
        instructions = instructions[:_RESPONSES_INSTRUCTIONS_MAX_CHARS]
    return instructions, _pair_missing_tool_results(items)


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
            from .responses_phase import replay_responses_output

            captured = (msg.metadata or {}).get("responses_output")
            # Text-only capture cannot validate attachments. Preserve this path's
            # existing conversion (which does not yet process Message.files).
            replay = (
                replay_responses_output(msg.content, captured)
                if captured and not (msg.files or msg.file_hashes)
                else None
            )
            if replay is not None:
                items.extend(replay)
                continue
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
                    "name": native_tool_name(tooluse.tool),
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
    if instructions and len(instructions) > _RESPONSES_INSTRUCTIONS_MAX_CHARS:
        logger.warning(
            "Truncating instructions to %d chars (Responses API cap)",
            _RESPONSES_INSTRUCTIONS_MAX_CHARS,
        )
        instructions = instructions[:_RESPONSES_INSTRUCTIONS_MAX_CHARS]
    return instructions, _pair_missing_tool_results(items)


@dataclass
class _ResponseFunctionCall:
    """Withhold executable syntax until streamed arguments can be reconciled."""

    name: str = ""
    call_id: str = ""
    item_id: str = ""
    arguments: str = ""
    complete_arguments: str | None = None
    invalid: bool = False

    def snapshot(self, item: Any) -> None:
        for key in ("name", "call_id"):
            value = _obj_get(item, key, "") or ""
            previous = getattr(self, key)
            if value and previous and value != previous:
                self.invalid = True
            if value:
                setattr(self, key, value)
        arguments = _obj_get(item, "arguments", None)
        if isinstance(arguments, str):
            if not arguments.startswith(self.arguments):
                self.invalid = True
            if (
                self.complete_arguments is not None
                and arguments != self.complete_arguments
            ):
                self.invalid = True
            self.complete_arguments = arguments
        if _obj_get(item, "status", None) == "incomplete":
            self.invalid = True

    def chunk(self) -> str:
        arguments = self.complete_arguments
        if arguments is None:
            arguments = self.arguments
        try:
            valid = isinstance(json.loads(arguments), dict)
        except (ValueError, TypeError):
            valid = False
        if (
            self.invalid
            or not valid
            or not self.name
            or not (self.call_id or self.item_id)
        ):
            logger.warning("Skipping incomplete or conflicting Responses function call")
            return ""
        return f"\n@{canonical_tool_name(self.name)}({self.call_id or self.item_id}): {arguments}"


class _ResponseFunctionCalls:
    """Resolve item IDs, call IDs and output positions without mixing calls."""

    def __init__(self) -> None:
        self.calls: list[_ResponseFunctionCall] = []
        self.items: dict[str, _ResponseFunctionCall] = {}
        self.ids: dict[str, _ResponseFunctionCall] = {}
        self.indices: dict[int, _ResponseFunctionCall] = {}

    def get(self, event: Any, item: Any = None) -> _ResponseFunctionCall:
        item_id = _obj_get(item, "id", "") or _obj_get(event, "item_id", "")
        call_id = _obj_get(item, "call_id", "") or _obj_get(event, "call_id", "")
        index = _obj_get(event, "output_index", None)
        # Older subscription events omit all routing fields for their sole call.
        if index is None and not item_id and not call_id:
            if len(self.calls) == 1:
                return self.calls[0]
            index = 0
        matches = [
            call
            for call in (
                self.items.get(item_id),
                self.ids.get(call_id),
                self.indices.get(index),
            )
            if call is not None
        ]
        if matches:
            call = matches[0]
            if any(other is not call for other in matches):
                for other in matches:
                    other.invalid = True
        elif (
            index is None
            and len(self.calls) == 1
            and not self.calls[0].item_id
            and not self.calls[0].call_id
        ):
            call = self.calls[0]
        else:
            call = _ResponseFunctionCall()
            self.calls.append(call)
        if item_id:
            if call.item_id and call.item_id != item_id:
                call.invalid = True
            call.item_id = item_id
            self.items[item_id] = call
        if call_id:
            if call.call_id and call.call_id != call_id:
                call.invalid = True
            call.call_id = call_id
            self.ids[call_id] = call
        if index is not None:
            self.indices[index] = call
        return call


def _completed_output_chunks(response_obj: Any) -> Generator[str, None, None]:
    """Rebuild text/tool-call chunks from a terminal response's ``output``."""
    for item in _obj_get(response_obj, "output", None) or []:
        item_type = _obj_get(item, "type")
        if item_type == "message":
            for part in _obj_get(item, "content", None) or []:
                if _obj_get(part, "type") == "output_text":
                    text = _obj_get(part, "text", "")
                    if text:
                        yield text
        elif item_type == "function_call":
            name = _obj_get(item, "name", "")
            call_id = _obj_get(item, "call_id", "") or _obj_get(item, "id", "")
            arguments = _obj_get(item, "arguments", "") or ""
            yield f"\n@{canonical_tool_name(name)}({call_id}): {arguments}"


def _stream_responses_events(
    event_iter: Iterable[Any],
    *,
    usage_callback: Callable[[Any], None] | None = None,
    model_callback: Callable[[str], None] | None = None,
    incomplete_callback: Callable[[], None] | None = None,
) -> Generator[str, None, None]:
    """Process a Responses API event stream, yielding formatted text chunks.

    Reconcile native calls with terminal output even after prose/reasoning
    deltas. Call syntax is withheld until reconciliation so partial or conflicting
    JSON can never become executable. Text retains the no-delta fallback without
    replaying text already streamed. Empty streams log shape, never content.
    """
    event_types: dict[str, int] = {}
    terminal: dict[str, Any] = {}
    calls = _ResponseFunctionCalls()

    def _counted(events: Iterable[Any]) -> Generator[Any, None, None]:
        for event in events:
            event_type = str(_obj_get(event, "type", "") or "?")
            event_types[event_type] = event_types.get(event_type, 0) + 1
            yield event

    emitted = False
    for chunk in _stream_responses_event_chunks(
        _counted(event_iter),
        usage_callback=usage_callback,
        model_callback=model_callback,
        incomplete_callback=incomplete_callback,
        terminal=terminal,
        calls=calls,
    ):
        emitted = emitted or bool(chunk)
        yield chunk

    response_obj = terminal.get("response")
    ordered_output: list[str | _ResponseFunctionCall] = []
    for index, item in enumerate(_obj_get(response_obj, "output", None) or []):
        if _obj_get(item, "type") == "function_call":
            call = calls.get({"output_index": index}, item)
            call.snapshot(item)
            if not any(previous is call for previous in ordered_output):
                ordered_output.append(call)
        elif not emitted and _obj_get(item, "type") == "message":
            ordered_output.extend(_completed_output_chunks({"output": [item]}))
    # Terminal order is authoritative; retain calls from delta-only providers.
    ordered_output.extend(
        call
        for call in calls.calls
        if not any(previous is call for previous in ordered_output)
    )
    # Render only after all snapshots: a later item can invalidate an earlier call.
    recovered: list[str] = []
    for part in ordered_output:
        chunk = part.chunk() if isinstance(part, _ResponseFunctionCall) else part
        if chunk:
            recovered.append(chunk)
    if recovered:
        logger.debug(
            "Responses stream reconciled %d chunk(s) from function calls/terminal output",
            len(recovered),
        )
        yield from recovered
        return
    if event_types and not emitted:
        logger.warning(
            "Responses stream produced no content: events=%s output_items=%s",
            event_types,
            [
                _obj_get(item, "type")
                for item in (_obj_get(response_obj, "output", None) or [])
            ],
        )


def _stream_responses_event_chunks(
    event_iter: Iterable[Any],
    *,
    usage_callback: Callable[[Any], None] | None = None,
    model_callback: Callable[[str], None] | None = None,
    incomplete_callback: Callable[[], None] | None = None,
    terminal: dict[str, Any] | None = None,
    calls: _ResponseFunctionCalls | None = None,
) -> Generator[str, None, None]:
    """Delta-level worker for :func:`_stream_responses_events`.

    Works with both OpenAI SDK event objects and raw SSE dicts from the
    subscription provider — ``_obj_get()`` handles both dict and attribute access.
    Accepts ``response.reasoning_text.delta`` (SDK) or ``response.reasoning.delta``
    (chatgpt.com backend) interchangeably.

    ``usage_callback`` is called with the usage object from ``response.completed``
    and ``response.done`` events (subscription streams use ``response.done``).

    ``model_callback`` is called with the provider-reported model id
    (``response.model``) whenever a ``response.created``,
    ``response.in_progress``, ``response.completed`` or ``response.done`` event
    carries a non-empty one, so callers keeping the last value get the
    completed/done model when present. On completed/done it fires before
    ``usage_callback`` so usage recording can include it.
    """
    saw_terminal = False
    in_reasoning_block = False
    seen_reasoning_delta = False
    in_duplicate_thinking_block = False
    pending = ""
    if calls is None:
        calls = _ResponseFunctionCalls()

    for event in event_iter:
        event_type = _obj_get(event, "type", "")

        if event_type in ("error", "response.failed"):
            # HTTP 200 only establishes the stream, not successful generation.
            # Preserve the provider code without dumping the response (instructions,
            # input and output may contain private context).
            if event_type == "response.failed":
                error = _obj_get(_obj_get(event, "response", None), "error", None)
            else:
                error = _obj_get(event, "error", None) or event
            code = _obj_get(error, "code", None) or "unknown_error"
            message = _obj_get(error, "message", None) or "Generation failed"
            raise ResponsesStreamError(event_type, code, message)

        elif event_type in (
            "response.reasoning_text.delta",
            "response.reasoning.delta",
        ):
            delta = _obj_get(event, "delta", "")
            if delta:
                if not in_reasoning_block:
                    if pending:
                        yield pending.replace("<thinking>", "<think>").replace(
                            "</thinking>", "</think>"
                        )
                        pending = ""
                    yield "<think>\n"
                    in_reasoning_block = True
                    seen_reasoning_delta = True
                yield delta

        elif event_type == "response.output_text.delta":
            if in_reasoning_block:
                yield "\n</think>\n"
                in_reasoning_block = False

            delta = _obj_get(event, "delta", "")
            if delta:
                text = pending + delta
                pending = ""

                if seen_reasoning_delta:
                    text, pending, in_duplicate_thinking_block = (
                        _filter_duplicate_thinking_text(
                            text, in_thinking_block=in_duplicate_thinking_block
                        )
                    )
                else:
                    for tag in ("<thinking>", "</thinking>"):
                        pending = _longest_suffix_prefix(text, tag)
                        if pending:
                            text = text[: -len(pending)]
                            break
                    text = text.replace("<thinking>", "<think>").replace(
                        "</thinking>", "</think>"
                    )

                if text:
                    yield text

        elif event_type in ("response.output_item.added", "response.output_item.done"):
            if in_reasoning_block:
                yield "\n</think>\n"
                in_reasoning_block = False
            if pending and not in_duplicate_thinking_block:
                yield pending.replace("<thinking>", "<think>").replace(
                    "</thinking>", "</think>"
                )
            pending = ""

            item = _obj_get(event, "item", None)
            if item is not None and _obj_get(item, "type") == "function_call":
                call = calls.get(event, item)
                # Added items carry a placeholder, not a completed snapshot.
                # Metadata can arrive after argument deltas.
                if event_type == "response.output_item.done":
                    call.snapshot(item)
                else:
                    call.snapshot(
                        {
                            "name": _obj_get(item, "name", ""),
                            "call_id": _obj_get(item, "call_id", ""),
                        }
                    )

        elif event_type == "response.function_call_arguments.delta":
            call = calls.get(event)
            delta = _obj_get(event, "delta", "")
            if delta:
                call.arguments += delta
                if (
                    call.complete_arguments is not None
                    and not call.complete_arguments.startswith(call.arguments)
                ):
                    call.invalid = True

        elif event_type == "response.function_call_arguments.done":
            calls.get(event).snapshot(event)

        elif event_type in ("response.created", "response.in_progress"):
            if model_callback is not None:
                served = served_model_from(_obj_get(event, "response", None))
                if served is not None:
                    model_callback(served)

        elif event_type in (
            "response.completed",
            "response.done",
            "response.incomplete",
        ):
            saw_terminal = True
            response_obj = _obj_get(event, "response", None)
            if terminal is not None:
                terminal["response"] = response_obj
            if event_type == "response.incomplete":
                details = _obj_get(response_obj, "incomplete_details", None)
                logger.warning(
                    "Responses API stream ended incomplete (reason=%s); "
                    "output may be truncated",
                    _obj_get(details, "reason", None) or "unknown",
                )
                if incomplete_callback is not None:
                    incomplete_callback()
            if model_callback is not None:
                served = served_model_from(response_obj)
                if served is not None:
                    model_callback(served)
            if usage_callback is not None:
                if response_obj is not None:
                    usage = _obj_get(response_obj, "usage", None)
                    if usage is not None:
                        usage_callback(usage)
            break

    # Reaching this point means real iterator exhaustion, not a consumer close.
    # Do not reconcile native arguments from an HTTP-200 truncated stream.
    if not saw_terminal:
        raise httpx.RemoteProtocolError(
            "Responses stream ended without terminal response.completed/response.done"
        )

    if in_reasoning_block:
        yield "\n</think>\n"
    if pending and not in_duplicate_thinking_block:
        yield pending.replace("<thinking>", "<think>").replace(
            "</thinking>", "</think>"
        )


def _extract_usage_token_counts(usage: Any) -> UsageTokenCounts:
    """Normalize Chat Completions and Responses API usage token fields.

    Handles both SDK objects (attribute access) and raw dicts (subscription SSE).
    """
    prompt_tokens = _obj_get(usage, "prompt_tokens", None)
    output_tokens = _obj_get(usage, "completion_tokens", None)
    details = _obj_get(usage, "prompt_tokens_details", None)
    _is_chat_completions = prompt_tokens is not None
    if prompt_tokens is None:
        prompt_tokens = _obj_get(usage, "input_tokens", None)
        details = _obj_get(usage, "input_tokens_details", None)
    if output_tokens is None:
        output_tokens = _obj_get(usage, "output_tokens", None)
    cache_read_tokens = (
        _obj_get(details, "cached_tokens", None) if details is not None else None
    )
    cache_creation_tokens = _obj_get(usage, "cache_creation_input_tokens", None)
    # OpenRouter nested shape: prompt_tokens_details.cache_write_tokens
    # Only for Chat Completions (not Responses API input_tokens_details.cache_write_tokens)
    if cache_creation_tokens is None and _is_chat_completions:
        cache_creation_tokens = (
            _obj_get(details, "cache_write_tokens", None)
            if details is not None
            else None
        )
    total_tokens = _obj_get(usage, "total_tokens", None)
    # Reasoning tokens: Chat Completions nests them under
    # completion_tokens_details, the Responses API under output_tokens_details.
    # OpenRouter (with usage accounting) mirrors the Chat Completions shape.
    output_details = _obj_get(usage, "completion_tokens_details", None)
    if output_details is None:
        output_details = _obj_get(usage, "output_tokens_details", None)
    reasoning_tokens = (
        _obj_get(output_details, "reasoning_tokens", None)
        if output_details is not None
        else None
    )
    if not isinstance(reasoning_tokens, int):
        reasoning_tokens = None

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
        reasoning_tokens=reasoning_tokens,
    )
