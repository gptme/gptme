from collections.abc import Generator
from dataclasses import dataclass, field
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

from .telemetry import trace_function


@dataclass(frozen=True)
class Codeblock:
    lang: str
    content: str
    path: str | None = None
    start: int | None = field(default=None, compare=False)

    def __post_init__(self):
        # init path if path is None and lang is pathy
        if self.path is None and self.is_filename:
            object.__setattr__(self, "path", self.lang)  # frozen dataclass workaround

    def to_markdown(self) -> str:
        return f"```{self.lang}\n{self.content}\n```"

    def to_xml(self) -> str:
        """Converts codeblock to XML with proper escaping."""
        # Use quoteattr for attributes to handle quotes and special chars safely
        # Use xml_escape for content to handle <, >, & characters
        return f"<codeblock lang={quoteattr(self.lang)} path={quoteattr(str(self.path))}>\n{xml_escape(self.content)}\n</codeblock>"

    @classmethod
    @trace_function(name="codeblock.from_markdown", attributes={"component": "parser"})
    def from_markdown(cls, content: str) -> "Codeblock":
        import re

        stripped = content.strip()
        fence_len = 0

        # Handle variable-length fences (3+ backticks)
        start_match = re.match(r"^(`{3,})", stripped)
        if start_match:
            fence_len = len(start_match.group(1))
            stripped = stripped[fence_len:]

        # Check for closing fence at end - only strip if fence lengths match
        end_match = re.search(r"(`{3,})$", stripped.strip())
        if end_match:
            end_fence_len = len(end_match.group(1))
            # Only strip closing fence if it matches opening fence length (CommonMark spec)
            if fence_len == end_fence_len:
                stripped = stripped.strip()[:-end_fence_len]

        lang = stripped.splitlines()[0].strip() if stripped.strip() else ""
        return cls(lang, stripped[len(lang) :].lstrip("\n") if lang else stripped)

    @classmethod
    @trace_function(name="codeblock.from_xml", attributes={"component": "parser"})
    def from_xml(cls, content: str) -> "Codeblock":
        """
        Example:
          <codeblock lang="python" path="example.py">
          print("Hello, world!")
          </codeblock>
        """
        root = ElementTree.fromstring(content)
        return cls(root.attrib["lang"], root.text or "", root.attrib.get("path"))

    @property
    def is_filename(self) -> bool:
        return "." in self.lang or "/" in self.lang

    @classmethod
    def iter_from_markdown(
        cls, markdown: str, streaming: bool = False
    ) -> list["Codeblock"]:
        """Extract codeblocks from markdown.

        Note: Tracing removed from this function as it's called hundreds of times
        per conversation, creating ~97% of all trace spans (see Issue #199).
        """
        return list(_extract_codeblocks(markdown, streaming=streaming))


import re


def _preprocess_inline_codeblocks(markdown: str) -> str:
    """Pre-process markdown to handle inline codeblocks.

    Some models (like Kimi K2.5) output codeblocks without newlines around them:
        I'll check the status```gh pr status```

    This function splits such patterns to ensure proper parsing:
        I'll check the status
        ```gh
        pr status
        ```
    """
    lines = markdown.split("\n")
    result_lines = []

    for line in lines:
        # Process multiple inline codeblocks on the same line
        # Pattern: text```lang content``` (inline block with both fences on same line)
        # We need to be careful not to break nested code blocks in string literals

        current_line = line
        line_parts = []

        while True:
            # Look for inline codeblock pattern: text```lang ... ```
            # Only match if:
            # 1. Preceded by non-whitespace (part of text)
            # 2. Followed by language name (word chars)
            # 3. Has matching closing fence on same line
            pattern = r"^(.*?)(\S)(`{3,})([a-zA-Z_][a-zA-Z0-9_]*)([^`]*?)(`{3,})(.*)$"
            match = re.match(pattern, current_line)

            if match:
                prefix = match.group(1) + match.group(2)
                fence = match.group(3)
                lang = match.group(4)
                code = match.group(5)
                closing = match.group(6)
                suffix = match.group(7)

                if prefix.strip():
                    line_parts.append(prefix)
                line_parts.append(f"{fence}{lang}")
                if code.strip():
                    line_parts.append(code.strip())
                line_parts.append(closing)

                # Continue processing the rest of the line
                current_line = suffix
            else:
                # No more inline codeblocks
                if current_line:
                    line_parts.append(current_line)
                break

        result_lines.extend(line_parts)

    return "\n".join(result_lines)


def _extract_codeblocks(
    markdown: str, streaming: bool = False
) -> Generator[Codeblock, None, None]:
    """
    Extracts code blocks from a markdown string using context-aware pattern matching.

    Note: Tracing removed from this function as it's called hundreds of times
    per conversation, creating ~97% of all trace spans (see Issue #199).

    Args:
        markdown: The markdown string to extract code blocks from
        streaming: If True, requires blank line after ``` to confirm block closure.
                   This prevents extracting incomplete blocks during streaming.

    Tricks used:
    - Opening ``` must be at start of line, optionally preceded by blank lines
    - Closing ``` must be alone on line, optionally followed by blank lines or EOF
    - ``` with content immediately before/after is treated as literal text, not delimiter

    This handles nested cases where ``` appears inside string literals or other content.
    """
    # Pre-process to handle inline codeblocks (models like Kimi K2.5)
    markdown = _preprocess_inline_codeblocks(markdown)

    # dont extract codeblocks from thinking blocks
    # (since claude sometimes forgets to close codeblocks in its thinking)
    think_end = markdown.find("</think>")
    if think_end != -1:
        # remove anything before and including </think> if it exists
        markdown = markdown[think_end + len("</think>") :]
    else:
        # if start <think> tag but no end, early exit
        if "<think>" in markdown:
            return

    # speed check (early exit): check if message contains a code block
    # Check for at least 2 fence markers (3+ backticks each)
    fence_pattern = re.compile(r"`{3,}")
    if len(fence_pattern.findall(markdown)) < 2:
        return

    lines = markdown.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Look for code block start (3+ backticks)
        # Count the backticks at the start of the line
        fence_match = re.match(r"^(`{3,})", line)
        if fence_match:
            fence_len = len(fence_match.group(1))
            start_line = i  # Track the starting line number
            lang = line[fence_len:].strip()
            content_lines: list[str] = []
            i += 1

            # Track nesting depth to handle nested code blocks
            nesting_depth = 1

            while i < len(lines):
                line = lines[i]

                # Check if this line has backticks at the start
                inner_fence_match = re.match(r"^(`{3,})", line)
                if inner_fence_match:
                    line_fence_len = len(inner_fence_match.group(1))

                    # Check if the rest of the line is empty (bare fence)
                    rest_of_line = line[line_fence_len:].strip()

                    if rest_of_line == "":
                        # This is a bare fence - could be closing or nested opening
                        if streaming:
                            # Streaming mode: require blank line to confirm closure
                            next_is_blank = (
                                i + 1 < len(lines) and lines[i + 1].strip() == ""
                            ) or (i + 1 >= len(lines))
                            if next_is_blank or i + 1 >= len(lines):
                                # Blank line or EOF confirms this is a closing tag
                                nesting_depth -= 1
                                if nesting_depth == 0:
                                    yield Codeblock(
                                        lang, "\n".join(content_lines), start=start_line
                                    )
                                    i += 1
                                    break
                                else:
                                    content_lines.append(line)
                            else:
                                # No blank line in streaming mode - this is opening a nested block
                                nesting_depth += 1
                                content_lines.append(line)
                        else:
                            # Not streaming: treat as closing
                            nesting_depth -= 1
                            if nesting_depth == 0:
                                # This closes our top-level block
                                yield Codeblock(
                                    lang, "\n".join(content_lines), start=start_line
                                )
                                i += 1  # Move past the closing ```
                                break
                            else:
                                # This closes a nested block, add to content
                                content_lines.append(line)
                    else:
                        # Line has content after backticks - check if it looks like a valid language tag
                        # to determine if it opens a nested block or is just content
                        potential_lang = line[line_fence_len:].strip()
                        # Valid language tags start with alphanumeric, underscore, slash, or dot
                        # They should NOT start with quotes or other special characters
                        # Examples of valid: python, js, save path/to/file.py, .env
                        # Examples of invalid: ''', "", ===
                        is_valid_lang = bool(potential_lang) and (
                            potential_lang[0].isalnum() or potential_lang[0] in "_/.~"
                        )
                        if is_valid_lang:
                            # This starts a nested block (has valid language tag)
                            nesting_depth += 1
                        # Either way, add to content (nested blocks appear as content)
                        content_lines.append(line)
                else:
                    content_lines.append(line)

                i += 1

            # If we reached the end without completing the block, don't yield it
            # (this handles the unfinished nested test case)
        else:
            i += 1
