"""Narrow, content-validated assistant phase roundtripping for Responses APIs.

Only text, optional phase strings and call-ID ordering anchors are retained.
Never persist provider output objects, reasoning, encrypted data or call arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from ..tools.base import ToolUse, find_json_end, toolcall_re
from ..tools.native_names import native_tool_name
from .openai_responses import _obj_get

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from ..message import (
        MessageMetadata,
        ResponsesCallItem,
        ResponsesOutput,
        ResponsesTextItem,
    )


_PROSE_MARKERS = re.compile(
    r"^ {0,3}(?P<fence>`{3,}(?!`)(?=[^`\n]*$)|~{3,})[^\n]*"
    r"|<(?P<think>think(?:ing)?)>"
    r"|<!--\s*think-sig:[\s\S]*?-->\s*",
    re.MULTILINE,
)


def _reasoning_spans(content: str) -> Iterator[tuple[int, int]]:
    """Find removable reasoning and signatures outside Markdown fences."""
    cursor = 0
    while match := _PROSE_MARKERS.search(content, cursor):
        cursor = match.end()
        if fence := match["fence"]:
            closer = re.compile(
                rf"^ {{0,3}}{fence[0]}{{{len(fence)},}}[ \t]*\r?$", re.MULTILINE
            )
            close = closer.search(content, cursor)
            cursor = close.end() if close else len(content)
            continue
        if tag := match["think"]:
            close_tag = f"</{tag}>"
            close_start = content.find(close_tag, cursor)
            cursor = close_start + len(close_tag) if close_start >= 0 else len(content)
        yield match.start(), cursor


def _normalize_prose(parts: list[str]) -> Iterator[str]:
    """Scan one prose stream and keep the original item or native-call offsets."""
    content = "".join(parts)
    spans = _reasoning_spans(content)
    span = next(spans, None)
    start = 0
    for part in parts:
        end = start + len(part)
        cursor = start
        kept = []
        while span is not None and span[0] < end:
            kept.append(content[cursor : span[0]])
            cursor = span[1]
            if cursor > end:
                break
            span = next(spans, None)
        kept.append(content[cursor:end])
        yield "".join(kept)
        start = end


def _native_parts(content: str) -> tuple[list[str], list[ToolUse], list[str]] | None:
    """Parse unchanged content, returning outside prose and exact native spans."""
    calls = [
        call for call in ToolUse.iter_from_content(content, "tool") if call.call_id
    ]
    prose: list[str] = []
    raw_calls: list[str] = []
    cursor = 0
    for call in calls:
        start = call.start
        if start is None or start < cursor:
            return None
        match = toolcall_re.match(content, start)
        end = find_json_end(content, match.start(3)) if match else None
        if end is None:
            return None
        prose.append(content[cursor:start])
        raw_calls.append(content[start:end])
        cursor = end
    prose.append(content[cursor:])
    return prose, calls, raw_calls


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _stripped_content(prose: list[str], raw_calls: list[str]) -> str | None:
    normalized = list(_normalize_prose(prose))
    if normalized == prose:
        return None
    leading = prose[0][: len(prose[0]) - len(prose[0].lstrip())]
    trailing = prose[-1][len(prose[-1].rstrip()) :]
    stripped = "".join(
        text + (raw_calls[i] if i < len(raw_calls) else "")
        for i, text in enumerate(normalized)
    )
    # Drop line breaks exposed at the edges by reasoning removal while
    # retaining original edge whitespace and any indentation on visible text.
    return (
        leading
        + stripped.removeprefix(leading).removesuffix(trailing).strip("\r\n")
        + trailing
    )


def strip_responses_reasoning(content: str) -> str:
    """Apply the same reasoning-only edit that phase replay can validate."""
    parts = _native_parts(content)
    if parts is not None:
        prose, _, raw_calls = parts
        if (stripped := _stripped_content(prose, raw_calls)) is not None:
            return stripped
    return content


def _comparison_text(text: str) -> str:
    # Streaming can normalize the spelling of thinking tags. The exact digest
    # still guards edits, and replay uses the captured item's original spelling.
    text = text.replace("<thinking>", "<think>").replace("</thinking>", "</think>")
    return re.sub(r"\n\n\n+", "\n\n", text).strip()


def _replay_arguments(call: ToolUse, raw_call: str) -> str:
    match = toolcall_re.match(raw_call)
    if match is not None:
        arguments = match[3]
        try:
            json.loads(arguments)
        except json.JSONDecodeError:
            pass  # Keep supporting legacy arguments accepted by JSON repair.
        else:
            return arguments
    return json.dumps(call.kwargs or {})


def replay_responses_output(
    content: str, output: object
) -> list[dict[str, Any]] | None:
    """Validate current text AND parsed calls before restoring original order.

    The tool parser may buffer calls until after all prose. Compare text and
    call sequences separately: output indexes, not flattened display positions,
    define replay order. All positions below are recomputed from current content.
    Malformed/hand-edited persistence data falls back to ordinary conversion.
    """
    if not isinstance(output, dict):
        return None
    digest = _digest(content)
    if digest not in (
        output.get("content_digest"),
        output.get("reasoning_stripped_digest"),
    ):
        return None
    captured = output.get("items")
    if not isinstance(captured, list):
        return None
    parts = _native_parts(content)
    if parts is None:
        return None
    text_parts, calls, raw_calls = parts
    # Calls and kwargs come from UNCHANGED current content. Strip reasoning only
    # after removing their spans, so literal tags/signatures in JSON survive.
    # Comparison only: normalize the same blank-line runs as each item below.
    # Replay text, current native kwargs and the exact edit digest stay untouched.
    remaining = _comparison_text("".join(_normalize_prose(text_parts)))
    items: list[dict[str, Any]] = []
    call_index = 0
    for item in captured:
        if not isinstance(item, dict):
            return None
        if item.get("type") == "message":
            text = item.get("text")
            if not isinstance(text, str):
                return None
            # Item whitespace is retained in replay, while display separators
            # and reasoning-strip whitespace are not item content.
            match = _comparison_text(text)
            remaining = remaining.lstrip()
            if not remaining.startswith(match):
                return None
            remaining = remaining[len(match) :]
            message: dict[str, Any] = {"role": "assistant", "content": text}
            if "phase" in item:
                if not isinstance(item["phase"], str):
                    return None
                message["phase"] = item["phase"]
            items.append(message)
        elif item.get("type") == "function_call":
            if call_index >= len(calls) or calls[call_index].call_id != item.get(
                "call_id"
            ):
                return None
            call = calls[call_index]
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": native_tool_name(call.tool),
                    "arguments": _replay_arguments(call, raw_calls[call_index]),
                }
            )
            call_index += 1
        else:
            return None
    return items if not remaining.strip() and call_index == len(calls) else None


class ResponsesPhaseCapture:
    """Observe text-item boundaries alongside (not inside) the tool parser."""

    def __init__(self) -> None:
        self.items: dict[int, ResponsesTextItem | ResponsesCallItem] = {}

    def output_item(self, index: int, item: Any) -> None:
        kind = _obj_get(item, "type")
        if kind == "message":
            previous = self.items.get(index)
            text = "".join(
                _obj_get(part, "text", "")
                if _obj_get(part, "type") == "output_text"
                else _obj_get(part, "refusal", "")
                if _obj_get(part, "type") == "refusal"
                else ""
                for part in (_obj_get(item, "content", None) or [])
            )
            captured: ResponsesTextItem = {"type": "message", "text": text}
            if previous and previous["type"] == "message":
                captured = {**previous, "text": text or previous["text"]}
            phase = _obj_get(item, "phase")
            if isinstance(phase, str):
                # SDK documents commentary/final_answer. Codex also has
                # partial_answer; unknown optional strings are preserved, not
                # coerced into either known value or an end-turn policy.
                captured["phase"] = phase
            self.items[index] = captured
        elif kind == "function_call":
            call_id = _obj_get(item, "call_id", "") or _obj_get(item, "id", "")
            if isinstance(call_id, str):
                self.items[index] = {"type": "function_call", "call_id": call_id}

    def events(self, events: Iterable[Any]) -> Iterator[Any]:
        for event in events:
            kind = _obj_get(event, "type")
            index = _obj_get(event, "output_index", 0)
            if kind in ("response.output_item.added", "response.output_item.done"):
                self.output_item(index, _obj_get(event, "item"))
            elif kind == "response.output_text.delta":
                item = self.items.get(index)
                if item and item["type"] == "message":
                    item["text"] += _obj_get(event, "delta", "") or ""
            elif kind in ("response.completed", "response.done", "response.incomplete"):
                response = _obj_get(event, "response")
                for i, output in enumerate(_obj_get(response, "output", None) or []):
                    self.output_item(i, output)
            yield event

    def metadata(
        self, metadata: MessageMetadata | None, content: str
    ) -> MessageMetadata | None:
        ordered = [item for _, item in sorted(self.items.items())]
        if not any(item["type"] == "message" and "phase" in item for item in ordered):
            return metadata
        # A terminal function item is not proof that the parser accepted it.
        # Keep only anchors for native calls actually present in current output.
        parts = _native_parts(content)
        if parts is None:
            return metadata
        prose, parsed_calls, raw_calls = parts
        calls = [call.call_id for call in parsed_calls]
        filtered: list[ResponsesTextItem | ResponsesCallItem] = []
        normalized = _normalize_prose(
            [item["text"] for item in ordered if item["type"] == "message"]
        )
        for item in ordered:
            if item["type"] == "function_call":
                if item["call_id"] not in calls:
                    continue
                calls.remove(item["call_id"])
            else:
                item = {**item, "text": next(normalized)}
            filtered.append(item)
        output: ResponsesOutput = {
            "content_digest": _digest(content),
            "items": filtered,
        }
        # Allow a prose-only reasoning-strip edit, never a transform of argument
        # strings. Every native span is copied byte-for-byte into this anchor.
        if (stripped := _stripped_content(prose, raw_calls)) is not None:
            output["reasoning_stripped_digest"] = _digest(stripped)
        if replay_responses_output(content, output) is None:
            return metadata
        return {**(metadata or {}), "responses_output": output}
