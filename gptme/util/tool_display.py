"""Terminal presentation of native tool calls, without changing tool content.

Native ``@tool(call-id): {...}`` spans are recognized with the same
``toolcall_re`` / ``find_json_end`` path as ``ToolUse.iter_from_content``.
The body is wrapped as a ``Codeblock`` so highlighting uses a codeblock
language instead of a one-off IPython decoder. Tools without a body
parameter fall back to pretty-printed JSON. Incomplete or invalid calls
stay literal. Raw messages are unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from ..codeblock import Codeblock
from ..tools.base import ToolUse, find_json_end, toolcall_re

_BODY_KEYS = ("code", "command", "content", "script")
_HIGHLIGHT_LANG = {
    "ipython": "python",
    "py": "python",
    "python": "python",
    "shell": "bash",
    "bash": "bash",
    "sh": "bash",
}
_EXT_LANG = {
    "py": "python",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "bash": "bash",
    "md": "markdown",
    "json": "json",
    "toml": "toml",
    "yml": "yaml",
    "yaml": "yaml",
    "rs": "rust",
}
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_PARTIAL_NATIVE = re.compile(r"@[\w.]*(?:\([\w\-:.]*\)?(?::\s*\{?)?)?")
_JSON_KEYWORDS = ("true", "false", "null")


def _json_prefix_viable(s: str, start: int) -> bool:
    """True if s[start:] can still complete as a JSON object.

    False means the fragment is already illegal, so this is not a live native call.
    """
    if start >= len(s) or s[start] != "{":
        return False
    in_string = False
    escape = False
    i = start
    n = len(s)
    while i < n:
        c = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c.isspace() or c in "{}[]:,":
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c in "-0123456789":
            i += 1
            while i < n and s[i] in "0123456789.eE+-":
                i += 1
            continue
        matched = False
        for kw in _JSON_KEYWORDS:
            if s.startswith(kw, i):
                i += len(kw)
                matched = True
                break
            if kw.startswith(s[i:]):
                return True
        if matched:
            continue
        return False
    return True


def _partial_native_hold_start(remaining: str) -> int | None:
    """Offset in remaining to keep unemitted, or None to flush it all.

    Holds a last-line partial header, and also ``@tool(id):\\n`` so a JSON object
    that arrives on the next chunk is still recognized as part of the call.
    """
    last_nl = remaining.rfind("\n")
    if last_nl < 0:
        return 0 if _PARTIAL_NATIVE.fullmatch(remaining) else None
    last_line = remaining[last_nl + 1 :]
    if _PARTIAL_NATIVE.fullmatch(last_line):
        return last_nl + 1
    if last_line.strip() == "":
        prev_nl = remaining.rfind("\n", 0, last_nl)
        prev_start = prev_nl + 1
        prev_line = remaining[prev_start:last_nl]
        if _PARTIAL_NATIVE.fullmatch(prev_line):
            return prev_start
    return None


def _skip_state(content: str) -> tuple[list[tuple[int, int]], int | None]:
    """Fenced skip ranges plus the start of an unclosed fence, if any."""
    ranges: list[tuple[int, int]] = []
    fence = ""
    start: int | None = None
    pos = 0
    while pos <= len(content):
        newline = content.find("\n", pos)
        line_end = len(content) if newline == -1 else newline
        match = _FENCE.fullmatch(content[pos:line_end])
        if match:
            mark, info = match.groups()
            if start is None:
                fence = mark
                start = pos
            elif mark[0] == fence[0] and len(mark) >= len(fence) and not info.strip():
                ranges.append((start, line_end))
                start = None
                fence = ""
        if newline == -1:
            break
        pos = newline + 1
    if start is not None:
        ranges.append((start, len(content)))
    return ranges, start


def _inside_skip(pos: int, ranges: list[tuple[int, int]]) -> int | None:
    for start, end in ranges:
        if start <= pos < end:
            return end
    return None


def _highlight_lang(tool_name: str, arguments: dict[str, Any]) -> str:
    lang = _HIGHLIGHT_LANG.get(tool_name, "text")
    if lang != "text":
        return lang
    path = arguments.get("path")
    if isinstance(path, str) and "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        return _EXT_LANG.get(ext, "text")
    return lang


def _display_from_tooluse(tool_use: ToolUse) -> ToolCodeDisplay:
    params = dict(tool_use._to_params())
    body: str | None = None
    if isinstance(tool_use.content, str) and tool_use.content:
        body = tool_use.content
    else:
        for key in _BODY_KEYS:
            value = params.get(key)
            if isinstance(value, str):
                body = params.pop(key)
                break
    lang = _highlight_lang(tool_use.tool, params)
    block = Codeblock(lang=lang, content=body or "")
    call_id = tool_use.call_id
    header = f"@{tool_use.tool}({call_id}):" if call_id else f"@{tool_use.tool}:"
    if body is None:
        return ToolCodeDisplay(
            header=header,
            code="",
            arguments={},
            fallback=params,
            lang=block.lang,
        )
    return ToolCodeDisplay(
        header=header,
        code=block.content,
        arguments=params,
        lang=block.lang,
    )


@dataclass
class ToolCodeDisplay:
    header: str
    code: str
    arguments: dict[str, Any]
    fallback: dict[str, Any] | None = None
    lang: str = "text"

    def render(self, highlight: bool = True) -> Group:
        parts: list[Text | Syntax] = [Text(self.header)]
        if self.fallback is not None:
            dumped = json.dumps(self.fallback, indent=2, ensure_ascii=False)
            parts.append(
                Syntax(dumped, "json", background_color="default", word_wrap=True)
                if highlight
                else Text(dumped)
            )
            return Group(*parts)
        if self.arguments:
            parts.append(
                Text("arguments: " + json.dumps(self.arguments, ensure_ascii=False))
            )
        if self.code:
            parts.append(
                Syntax(self.code, self.lang, background_color="default", word_wrap=True)
                if highlight
                else Text(self.code)
            )
        return Group(*parts)


@dataclass
class ToolCallDisplay:
    """Split terminal chunks into ordinary text and complete native tool calls.

    Complete ``@tool(call-id)`` JSON objects are projected. Other text keeps
    streaming, and fenced examples are left alone. ``finish`` returns any
    incomplete call verbatim, including when generation was interrupted.
    """

    _buf: str = field(default="", init=False, repr=False)
    _emitted: int = field(default=0, init=False, repr=False)

    def feed(self, text: str) -> Iterator[str | Text | ToolCodeDisplay]:
        self._buf += text
        yield from self._drain()
        self._compact()

    def finish(self) -> str:
        leftover = self._buf[self._emitted :]
        self._buf = ""
        self._emitted = 0
        return leftover

    def _compact(self) -> None:
        """Drop emitted prefix; keep a partial native call or unclosed fence."""
        if self._emitted <= 0:
            return
        keep_from = self._emitted
        _, open_start = _skip_state(self._buf)
        if open_start is not None and open_start < keep_from:
            keep_from = open_start
        if keep_from <= 0:
            return
        self._buf = self._buf[keep_from:]
        self._emitted -= keep_from

    def _drain(self) -> Iterator[str | Text | ToolCodeDisplay]:
        skip, _ = _skip_state(self._buf)
        while self._emitted < len(self._buf):
            match = None
            json_end: int | None = None
            search_from = self._emitted
            while found := toolcall_re.search(self._buf, search_from):
                skipped_until = _inside_skip(found.start(), skip)
                if skipped_until is not None:
                    search_from = skipped_until
                    continue
                candidate_end = find_json_end(self._buf, found.start(3))
                if candidate_end is None and not _json_prefix_viable(
                    self._buf, found.start(3)
                ):
                    # Abandoned/invalid JSON: not a live native call.
                    search_from = found.start() + 1
                    continue
                match = found
                json_end = candidate_end
                break

            if match is None:
                remaining = self._buf[self._emitted :]
                hold = _partial_native_hold_start(remaining)
                if hold is not None:
                    hold_abs = self._emitted + hold
                    if _inside_skip(hold_abs, skip) is None:
                        if hold > 0:
                            yield remaining[:hold]
                            self._emitted += hold
                        return
                yield remaining
                self._emitted = len(self._buf)
                return

            if match.start() > self._emitted:
                yield self._buf[self._emitted : match.start()]
                self._emitted = match.start()

            if json_end is None:
                return

            raw = self._buf[match.start() : json_end]
            json_str = self._buf[match.start(3) : json_end]
            try:
                kwargs = json.loads(json_str)
            except (ValueError, RecursionError):
                kwargs = None
            if isinstance(kwargs, dict):
                yield _display_from_tooluse(
                    ToolUse(
                        match.group(1),
                        None,
                        None,
                        kwargs=kwargs,
                        call_id=match.group(2),
                        start=match.start(),
                        _format="tool",
                    )
                )
            else:
                yield Text(raw)
            self._emitted = json_end
