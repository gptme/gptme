"""Helpers for sanitizing user-visible streamed output."""


class VisibleOutputSanitizer:
    """Hide reasoning blocks from user-visible streamed output."""

    _OPENING_TAGS = {"<think>", "<thinking>"}
    _CLOSING_TAGS = {"</think>", "</thinking>"}

    def __init__(self) -> None:
        self._in_thinking = False
        self._just_closed_thinking = False
        self._raw_line: list[str] = []
        self._visible_line: list[str] = []

    def feed(self, text: str) -> str:
        """Process streamed text and return any newly visible chunk."""
        visible_parts: list[str] = []

        for char in text:
            if char != "\n":
                self._raw_line.append(char)
                if not self._in_thinking:
                    self._visible_line.append(char)
                continue

            line = "".join(self._raw_line)
            prev_thinking = self._in_thinking

            if line in self._OPENING_TAGS:
                self._in_thinking = True
            elif line in self._CLOSING_TAGS:
                self._in_thinking = False
                self._just_closed_thinking = True

            if not self._in_thinking and not prev_thinking:
                if self._visible_line:
                    visible_parts.append("".join(self._visible_line) + "\n")
                elif not self._just_closed_thinking:
                    visible_parts.append("\n")
                self._just_closed_thinking = False

            self._raw_line.clear()
            self._visible_line.clear()

        return "".join(visible_parts)

    def finish(self) -> str:
        """Flush any remaining visible content at end-of-stream."""
        line = "".join(self._raw_line)
        self._raw_line.clear()

        if self._in_thinking or line in self._OPENING_TAGS | self._CLOSING_TAGS:
            self._visible_line.clear()
            self._just_closed_thinking = False
            return ""

        visible = "".join(self._visible_line)
        self._visible_line.clear()
        self._just_closed_thinking = False
        return visible
