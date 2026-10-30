"""Narrow, content-validated assistant phase roundtripping for Responses APIs.

Only text, optional phase strings and call-ID ordering anchors are retained.
Never persist provider output objects, reasoning, encrypted data or call arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from ..message import _strip_think_sig
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


def _prose(content: str) -> str:
    """Normalize only prose; never apply reasoning regexes to native JSON."""
    if re.search(r"<think(?:ing)?>", content):
        from ..context import strip_reasoning

        # The shared utility strips edges too. Retain separators around native
        # calls so removing reasoning cannot glue prose to an @call header.
        leading = content[: len(content) - len(content.lstrip())]
        trailing = content[len(content.rstrip()) :]
        content, _ = strip_reasoning(content)
        # An interrupted reasoning block is not replayable visible text either.
        content = re.sub(r"<think(?:ing)?>.*$", "", content, flags=re.DOTALL)
        content = leading + content + trailing
    return _strip_think_sig(content)


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
    text_parts, calls, _ = parts
    # Calls and kwargs come from UNCHANGED current content. Strip reasoning only
    # after removing their spans, so literal tags/signatures in JSON survive.
    # Comparison only: normalize the same blank-line runs as each item below.
    # Replay text, current native kwargs and the exact edit digest stay untouched.
    remaining = re.sub(r"\n\n\n+", "\n\n", _prose("".join(text_parts))).strip()
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
            text = _prose(text)
            match = re.sub(r"\n\n\n+", "\n\n", text).strip()
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
            call_index += 1
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": native_tool_name(call.tool),
                    "arguments": json.dumps(call.kwargs or {}),
                }
            )
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
        for item in ordered:
            if item["type"] == "function_call":
                if item["call_id"] not in calls:
                    continue
                calls.remove(item["call_id"])
            else:
                item = {**item, "text": _prose(item["text"])}
            filtered.append(item)
        output: ResponsesOutput = {
            "content_digest": _digest(content),
            "items": filtered,
        }
        # Allow a prose-only reasoning-strip edit, never a transform of argument
        # strings. Every native span is copied byte-for-byte into this anchor.
        normalized = [_prose(text) for text in prose]
        if normalized != prose:
            stripped = "".join(
                text + (raw_calls[i] if i < len(raw_calls) else "")
                for i, text in enumerate(normalized)
            ).strip()
            output["reasoning_stripped_digest"] = _digest(stripped)
        if replay_responses_output(content, output) is None:
            return metadata
        return {**(metadata or {}), "responses_output": output}
