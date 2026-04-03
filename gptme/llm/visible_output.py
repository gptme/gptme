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
            stripped = line.strip()

            if stripped.lower() in self._OPENING_TAGS:
                self._in_thinking = True
            elif stripped.lower() in self._CLOSING_TAGS:
                self._in_thinking = False
                self._just_closed_thinking = True
            elif self._in_thinking:
                # Handle closing tag followed by inline content on the same line,
                # e.g. "</think>visible text".  The suffix after the tag is visible.
                # Sort longest-first so "</thinking>" is checked before its prefix "</think>".
                for ctag in sorted(self._CLOSING_TAGS, key=len, reverse=True):
                    if stripped.lower().startswith(ctag):
                        self._in_thinking = False
                        self._just_closed_thinking = True
                        after_tag = stripped[len(ctag) :]
                        if after_tag:
                            visible_parts.append(after_tag + "\n")
                        break

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
        stripped = line.strip()

        if stripped.lower() in self._OPENING_TAGS | self._CLOSING_TAGS:
            self._visible_line.clear()
            self._just_closed_thinking = False
            return ""

        if self._in_thinking:
            # Handle closing tag with inline content on the final line,
            # e.g. "</think>visible text" with no trailing newline.
            # Sort longest-first so "</thinking>" is checked before its prefix "</think>".
            for ctag in sorted(self._CLOSING_TAGS, key=len, reverse=True):
                if stripped.lower().startswith(ctag):
                    after_tag = stripped[len(ctag) :]
                    self._visible_line.clear()
                    self._just_closed_thinking = False
                    return after_tag
            self._visible_line.clear()
            self._just_closed_thinking = False
            return ""

        visible = "".join(self._visible_line)
        self._visible_line.clear()
        self._just_closed_thinking = False
        return visible
