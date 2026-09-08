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


def _display_skip_ranges(content: str) -> list[tuple[int, int]]:
    """Fenced regions that must keep native calls literal, including an unclosed fence."""
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
    return ranges


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

    def finish(self) -> str:
        leftover = self._buf[self._emitted :]
        self._buf = ""
        self._emitted = 0
        return leftover

    def _drain(self) -> Iterator[str | Text | ToolCodeDisplay]:
        while self._emitted < len(self._buf):
            skip = _display_skip_ranges(self._buf)
            match = None
            search_from = self._emitted
            while found := toolcall_re.search(self._buf, search_from):
                skipped_until = _inside_skip(found.start(), skip)
                if skipped_until is not None:
                    search_from = skipped_until
                    continue
                match = found
                break

            if match is None:
                remaining = self._buf[self._emitted :]
                last_nl = remaining.rfind("\n")
                last_line = remaining[last_nl + 1 :]
                last_line_start = self._emitted + last_nl + 1
                if last_nl < 0:
                    last_line_start = self._emitted
                if (
                    _PARTIAL_NATIVE.fullmatch(last_line)
                    and _inside_skip(last_line_start, skip) is None
                ):
                    if last_nl >= 0:
                        yield remaining[: last_nl + 1]
                        self._emitted += last_nl + 1
                    return
                yield remaining
                self._emitted = len(self._buf)
                return

            if match.start() > self._emitted:
                yield self._buf[self._emitted : match.start()]
                self._emitted = match.start()

            json_end = find_json_end(self._buf, match.start(3))
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
