from collections.abc import Generator
from dataclasses import dataclass, field
from xml.etree import ElementTree

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
        return f'<codeblock lang="{self.lang}" path="{self.path}">\n{self.content}\n</codeblock>'

    @classmethod
    @trace_function(name="codeblock.from_markdown", attributes={"component": "parser"})
    def from_markdown(cls, content: str) -> "Codeblock":
        if content.strip().startswith("```"):
            content = content[3:]
        if content.strip().endswith("```"):
            content = content[:-3]
        lang = content.splitlines()[0].strip()
        return cls(lang, content[len(lang) :])

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
    @trace_function(
        name="codeblock.iter_from_markdown", attributes={"component": "parser"}
    )
    def iter_from_markdown(
        cls, markdown: str, streaming: bool = False
    ) -> list["Codeblock"]:
        return list(_extract_codeblocks(markdown, streaming=streaming))


import re

# valid start/end of markdown code blocks
re_triple_tick_start = re.compile(r"^```.*\n")
re_triple_tick_end = re.compile(r"^```$")


@trace_function(name="codeblock.extract_codeblocks", attributes={"component": "parser"})
def _extract_codeblocks(
    markdown: str, streaming: bool = False
) -> Generator[Codeblock, None, None]:
    """
    Extracts code blocks from a markdown string using context-aware pattern matching.

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
    if markdown.count("```") < 2:
        return

    lines = markdown.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Look for code block start
        if line.startswith("```"):
            start_line = i  # Track the starting line number
            lang = line[3:].strip()
            content_lines: list[str] = []
            i += 1

            # Track nesting depth to handle nested code blocks
            nesting_depth = 1

            # Collect content until we find the matching closing ```
            while i < len(lines):
                line = lines[i]

                # Check if this line starts with ``` (potential opening or closing)
                if line.startswith("```"):
                    if line.strip() == "```":
                        # Bare ``` - determine if opening or closing based on context

                        # Check if previous line is a header-like structure
                        # (headers followed by bare ``` are usually documentation/examples,
                        # not code block delimiters)
                        prev_line = content_lines[-1] if content_lines else ""
                        prev_line_stripped = prev_line.strip()
                        is_after_header = (
                            # Bold text with colon (e.g., "**Structure:**")
                            (
                                prev_line_stripped.startswith("**")
                                and prev_line_stripped.endswith(":**")
                            )
                            # Markdown headers (e.g., "## Subtitle")
                            or prev_line_stripped.startswith("#")
                        )

                        # Check next line
                        has_next_line = i + 1 < len(lines)
                        next_has_content = has_next_line and lines[i + 1].strip() != ""
                        next_is_blank = has_next_line and lines[i + 1].strip() == ""
                        next_is_fence = has_next_line and lines[i + 1].startswith("```")

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
                            # Check if this might be after a header with no nearby nested closing
                            # (common pattern in autonomous run failures)
                            if is_after_header:
                                # Look ahead for a VERY nearby closing fence (within 5 lines)
                                # If not found nearby, treat as literal content
                                has_nearby_closing = False
                                lookahead_limit = min(
                                    i + 7, len(lines)
                                )  # Within 6 lines
                                for j in range(i + 1, lookahead_limit):
                                    if lines[j].strip() == "```":
                                        has_nearby_closing = True
                                        break

                                if not has_nearby_closing:
                                    # No nearby closing fence, treat as literal content
                                    content_lines.append(line)
                                else:
                                    # Has nearby closing, this is a valid nested block
                                    nesting_depth += 1
                                    content_lines.append(line)
                            else:
                                # Next line has content, this is an opening tag
                                nesting_depth += 1
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
                        # This starts a nested block (has language or content after ```)
                        nesting_depth += 1
                        content_lines.append(line)
                else:
                    content_lines.append(line)

                i += 1

            # If we reached the end without completing the block, don't yield it
            # (this handles the unfinished nested test case)
        else:
            i += 1
