from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from dataclasses import dataclass

from ..config import get_config
from ..message import Message, get_output_format, set_output_format
from ..util.tokens import len_tokens

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_DEFAULT_THRESHOLD_TOKENS = 500
_FULL_OUTPUT_HINTS = (
    "full output",
    "entire output",
    "whole output",
    "show everything",
    "full file",
    "entire file",
    "whole file",
)


@dataclass(frozen=True)
class PrunePlan:
    ranges: tuple[tuple[int, int], ...]
    total_lines: int
    kept_lines: int
    original_tokens: int
    kept_tokens: int
    model: str

    def apply(self, lines: list[str]) -> str:
        selected: list[str] = []
        for start, end in self.ranges:
            selected.extend(lines[start - 1 : end])
        return "\n".join(selected)


def _estimate_tokens(text: str, model: str) -> int:
    try:
        return len_tokens(text, model)
    except Exception:
        return max(1, len(text) // 4)


def _threshold_tokens() -> int:
    raw = get_config().get_env("PRUNE_TOOL_OUTPUT_THRESHOLD_TOKENS")
    if not raw:
        return _DEFAULT_THRESHOLD_TOKENS
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Invalid PRUNE_TOOL_OUTPUT_THRESHOLD_TOKENS value %r, using %d",
            raw,
            _DEFAULT_THRESHOLD_TOKENS,
        )
        return _DEFAULT_THRESHOLD_TOKENS
    return parsed if parsed > 0 else _DEFAULT_THRESHOLD_TOKENS


def _resolve_model_name() -> str | None:
    override = get_config().get_env("PRUNE_TOOL_OUTPUT_MODEL")
    if override:
        return override

    from ..llm.models import get_default_model_summary

    if summary_model := get_default_model_summary():
        return summary_model.full

    config = get_config()
    return (config.chat and config.chat.model) or config.get_env("MODEL")


def _latest_user_query() -> str | None:
    from ..logmanager import LogManager

    manager = LogManager.get_current_log()
    if not manager:
        return None
    for msg in reversed(manager.log.messages):
        if msg.role == "user" and msg.content.strip():
            return msg.content
    return None


def _normalize_ranges(
    ranges: list[tuple[int, int]], total_lines: int
) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start < 1 or end < start or end > total_lines:
            continue
        if merged and start <= merged[-1][1] + 1:
            prev_start, prev_end = merged[-1]
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _parse_ranges(response: str, total_lines: int) -> list[tuple[int, int]] | None:
    match = _JSON_OBJECT_RE.search(response)
    if not match:
        return None

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    raw_ranges = payload.get("ranges")
    if not isinstance(raw_ranges, list):
        return None

    ranges = [
        (item[0], item[1])
        for item in raw_ranges
        if (
            isinstance(item, list | tuple)
            and len(item) == 2
            and isinstance(item[0], int)
            and isinstance(item[1], int)
        )
    ]
    normalized = _normalize_ranges(ranges, total_lines)
    return normalized or None


@contextmanager
def _quiet_llm_output():
    previous = get_output_format()
    set_output_format("quiet")
    try:
        yield
    finally:
        set_output_format(previous)


def _should_skip(query: str) -> bool:
    lowered = query.lower()
    return any(hint in lowered for hint in _FULL_OUTPUT_HINTS)


def plan_tool_output_prune(
    tool_name: str,
    output: str,
    *,
    context_label: str | None = None,
) -> PrunePlan | None:
    if not get_config().get_env_bool("PRUNE_TOOL_OUTPUT", default=False):
        return None

    lines = output.splitlines()
    if len(lines) < 2:
        return None

    query = _latest_user_query()
    if not query or _should_skip(query):
        return None

    model = _resolve_model_name()
    if not model:
        return None

    original_tokens = _estimate_tokens(output, model)
    if original_tokens < _threshold_tokens():
        return None

    numbered_output = "\n".join(f"{i}\t{line}" for i, line in enumerate(lines, start=1))
    prompt = (
        "Select the minimal exact line ranges from this tool output that are needed "
        "to answer the latest user request. Return strict JSON only in the form "
        '{"ranges":[[start,end],...]}.\n'
        "Rules:\n"
        "- 1-indexed inclusive line ranges.\n"
        "- Keep exact lines only; no paraphrasing or rewriting.\n"
        "- If unsure, keep more rather than less.\n"
        "- If almost everything matters, return the full range.\n\n"
        f"Latest user request:\n{query}\n\n"
        f"Tool: {tool_name}\n"
    )
    if context_label:
        prompt += f"Context: {context_label}\n"
    prompt += f"\nTool output ({len(lines)} lines):\n{numbered_output}"

    messages = [
        Message(
            "system",
            "You are a precise extraction helper for coding-agent tool output.",
        ),
        Message("user", prompt),
    ]

    try:
        from ..llm import reply as llm_reply

        with _quiet_llm_output():
            response = llm_reply(messages, model=model, stream=False, tools=None)
    except Exception as exc:
        logger.debug("Tool-output pruning skipped after LLM failure: %s", exc)
        return None

    ranges = _parse_ranges(response.content, total_lines=len(lines))
    if not ranges:
        return None

    kept_lines = sum(end - start + 1 for start, end in ranges)
    if kept_lines >= len(lines):
        return None

    selected_text = "\n".join(
        line for start, end in ranges for line in lines[start - 1 : end]
    )
    kept_tokens = _estimate_tokens(selected_text, model)
    if kept_tokens >= original_tokens:
        return None

    return PrunePlan(
        ranges=tuple(ranges),
        total_lines=len(lines),
        kept_lines=kept_lines,
        original_tokens=original_tokens,
        kept_tokens=kept_tokens,
        model=model,
    )
