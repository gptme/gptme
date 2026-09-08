"""Terminal presentation of native IPython calls, without changing tool content."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

_PREFIX = "@ipython("
_HEADER = re.compile(r"@ipython\([\w\-:.]+\):\s*\{")
_PARTIAL_HEADER = re.compile(r"@ipython\([\w\-:.]*(?:\)(?::\s*)?)?")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")


@dataclass
class ToolCodeDisplay:
    header: str
    code: str
    arguments: dict[str, Any]

    def render(self, highlight: bool = True) -> Group:
        parts: list[Text | Syntax] = [Text(self.header)]
        if self.arguments:
            parts.append(
                Text("arguments: " + json.dumps(self.arguments, ensure_ascii=False))
            )
        parts.append(
            Syntax(self.code, "python", background_color="default", word_wrap=True)
            if highlight
            else Text(self.code)
        )
        return Group(*parts)


class ToolCallDisplay:
    """Split terminal chunks into ordinary text and complete, strict native calls.

    Only an IPython header and its JSON object are buffered. Other text keeps
    streaming, and fenced examples are left alone. ``finish`` returns any
    incomplete call verbatim, including when generation was interrupted.
    """

    def __init__(self) -> None:
        self._pending: list[str] = []
        self._header = ""
        self._depth = 0
        self._in_string = False
        self._escaped = False
        self._line: list[str] = []
        self._fence = ""

    def _record_text(self, char: str) -> None:
        if char != "\n":
            self._line.append(char)
            return
        if match := _FENCE.fullmatch("".join(self._line)):
            fence, tail = match.groups()
            if not self._fence:
                self._fence = fence
            elif (
                fence[0] == self._fence[0]
                and len(fence) >= len(self._fence)
                and not tail.strip()
            ):
                self._fence = ""
        self._line.clear()

    def feed(self, text: str) -> Iterator[str | Text | ToolCodeDisplay]:
        plain: list[str] = []
        for char in text:
            if not self._pending:
                if char != "@" or self._line or self._fence:
                    plain.append(char)
                    self._record_text(char)
                    continue
            self._pending.append(char)
            if not self._header:
                candidate = "".join(self._pending)
                if _HEADER.fullmatch(candidate):
                    self._header = candidate[:-1].rstrip()
                    self._depth = 1
                    continue
                if _PREFIX.startswith(candidate) or _PARTIAL_HEADER.fullmatch(
                    candidate
                ):
                    continue
                raw = self.finish()
                plain.append(raw)
                for c in raw:
                    self._record_text(c)
                continue

            if self._escaped:
                self._escaped = False
            elif self._in_string and char == "\\":
                self._escaped = True
            elif char == '"':
                self._in_string = not self._in_string
            elif not self._in_string:
                self._depth += (char == "{") - (char == "}")
            if self._depth:
                continue

            header = self._header
            raw = self.finish()
            try:
                arguments = json.loads(raw[raw.index("{") :])
            except (ValueError, RecursionError):
                arguments = None
            if plain:
                yield "".join(plain)
                plain.clear()
            if isinstance(arguments, dict) and isinstance(arguments.get("code"), str):
                code = arguments.pop("code")
                yield ToolCodeDisplay(header, code, arguments)
                # A call is an opaque span: its JSON strings cannot open fences.
                self._line[:] = ["@"]
            else:
                # Invalid/unsupported calls remain literal, including Rich markup
                # and Markdown fences inside their argument strings.
                yield Text(raw)
                for c in raw:
                    self._record_text(c)
        if plain:
            yield "".join(plain)

    def finish(self) -> str:
        raw = "".join(self._pending)
        self._pending.clear()
        self._header = ""
        self._depth = 0
        self._in_string = False
        self._escaped = False
        return raw
