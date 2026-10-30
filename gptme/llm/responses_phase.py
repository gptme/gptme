"""Narrow, content-validated assistant phase roundtripping for Responses APIs.

Only text, optional phase strings and call-ID ordering anchors are retained.
Never persist provider output objects, reasoning, encrypted data or call arguments.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

from ..tools.base import ToolUse, find_json_end
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


def _visible(content: str) -> str:
    """Match the existing reasoning-strip transform, without storing reasoning."""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    content = re.sub(r"<thinking>.*?</thinking>", "", content, flags=re.DOTALL)
    return re.sub(r"\n\n\n+", "\n\n", content).strip()


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
    visible = _visible(content)
    calls = [c for c in ToolUse.iter_from_content(visible, "tool") if c.call_id]
    # Remove only native calls actually accepted by the parser. Never source a
    # call or its arguments from terminal metadata.
    text_parts: list[str] = []
    cursor = 0
    for call in calls:
        start = call.start
        if start is None or start < cursor:
            return None
        end = find_json_end(visible, visible.find("{", start))
        if end is None:
            return None
        text_parts.append(visible[cursor:start])
        cursor = end
    text_parts.append(visible[cursor:])
    remaining = "".join(text_parts).strip()
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
        calls = [
            c.call_id
            for c in ToolUse.iter_from_content(_visible(content), "tool")
            if c.call_id
        ]
        filtered: list[ResponsesTextItem | ResponsesCallItem] = []
        for item in ordered:
            if item["type"] == "function_call":
                if item["call_id"] not in calls:
                    continue
                calls.remove(item["call_id"])
            filtered.append(item)
        output: ResponsesOutput = {
            "content_digest": _digest(content),
            "items": filtered,
        }
        # Only the exact existing reasoning-strip transform is an allowed edit.
        # Other changes (including whitespace) must not resurrect captured text.
        stripped = _visible(content)
        if re.search(r"<think(?:ing)?>", content):
            output["reasoning_stripped_digest"] = _digest(stripped)
        if replay_responses_output(content, output) is None:
            return metadata
        return {**(metadata or {}), "responses_output": output}
