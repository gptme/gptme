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

            # Collect content until we find the matching closing ```
            while i < len(lines):
                line = lines[i]

                # Check if this line starts with backticks (potential opening or closing)
                line_fence_match = re.match(r"^(`{3,})", line)
                if line_fence_match:
                    line_fence_len = len(line_fence_match.group(1))
                    # Check if this is a bare fence (only backticks on the line)
                    is_bare_fence = line.strip() == "`" * line_fence_len
                    # For closing the outer block, need exact match of opening fence length
                    # For inner nested blocks, any bare fence can close them
                    is_outer_close = is_bare_fence and line_fence_len == fence_len
                    if is_outer_close or (is_bare_fence and nesting_depth > 1):
                        # Bare fence - determine if opening or closing based on context

                        # Check next line
                        has_next_line = i + 1 < len(lines)
                        next_has_content = has_next_line and lines[i + 1].strip() != ""
                        next_is_blank = has_next_line and lines[i + 1].strip() == ""
                        next_is_fence = has_next_line and bool(
                            re.match(r"^`{3,}", lines[i + 1])
                        )

                        # Decision logic:
                        # 1. If we have nested blocks open (depth > 1), prefer closing
                        #    This fixes the case where ``` appears after a nested block
                        #    like ```text, where it should close that block.
                        # 2. If next line has content and isn't a fence -> opening
                        # 3. If streaming mode:
                        #    - Require blank line after ``` to confirm closure
                        #    - Otherwise treat as incomplete (don't extract)
                        # 4. If not streaming:
                        #    - Blank line or EOF -> closing

                        if nesting_depth > 1:
                            # We have nested blocks open, this should close the innermost one
                            nesting_depth -= 1
                            if nesting_depth == 0:
                                # Check streaming condition before yielding
                                if streaming and not next_is_blank:
                                    # Streaming mode requires blank line to confirm closure
                                    # Incomplete block - don't extract
                                    break
                                # Either not streaming, or streaming with blank line - extract
                                yield Codeblock(
                                    lang, "\n".join(content_lines), start=start_line
                                )
                                i += 1
                                break
                            else:
                                content_lines.append(line)
                        elif next_has_content and not next_is_fence:
                            # Next line has content - check if this is a real nested block
                            if nesting_depth > 1:
                                # We're already nested, this opens another level
                                nesting_depth += 1
                                content_lines.append(line)
                            elif nesting_depth == 1:
                                # At depth 1, look ahead to see if there's a matching closing fence
                                # This distinguishes real nested blocks from bare backticks in content
                                has_closing_fence = False
                                for j in range(i + 1, min(i + 20, len(lines))):
                                    # Check if this line is a bare fence (only backticks)
                                    inner_fence_match = re.match(
                                        r"^(`{3,})$", lines[j].strip()
                                    )
                                    if inner_fence_match:
                                        # Found a bare fence
                                        # Check if there's content after it (allowing blank lines)
                                        # Look ahead a few more lines to see if outer block continues
                                        has_more_content = False
                                        for k in range(j + 1, min(j + 5, len(lines))):
                                            if lines[k].strip() != "":
                                                # Found non-blank content after closing fence
                                                has_more_content = True
                                                break

                                        if has_more_content:
                                            # This looks like a nested block: opening, content, closing, more content
                                            has_closing_fence = True
                                        break
                                    elif (
                                        re.match(r"^`{3,}", lines[j])
                                        and len(lines[j].strip()) > 3
                                    ):
                                        # Hit a language-tagged fence, stop looking
                                        break

                                if has_closing_fence:
                                    # Looks like a real nested block
                                    nesting_depth += 1
                                    content_lines.append(line)
                                else:
                                    # No matching fence found, treat as literal content
                                    content_lines.append(line)
                            else:
                                content_lines.append(line)
                        elif streaming:
                            # Streaming mode: require blank line to confirm closure
                            if next_is_blank:
                                # Blank line confirms this is a closing tag
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
                                # No blank line in streaming mode - incomplete block
                                # Don't extract, treat as opening to keep block open
                                nesting_depth += 1
                                content_lines.append(line)
                        else:
                            # Not streaming: blank line, EOF, or other -> closing
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
