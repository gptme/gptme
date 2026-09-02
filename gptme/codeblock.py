import re
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
    fence: str = field(default_factory=lambda: "```", compare=False, repr=False)

    def __post_init__(self):
        # init path if path is None and lang is pathy
        if self.path is None and self.is_filename:
            object.__setattr__(self, "path", self.lang)  # frozen dataclass workaround

    def to_markdown(self) -> str:
        return f"{self.fence}{self.lang}\n{self.content}\n{self.fence}"

    def to_xml(self) -> str:
        """Converts codeblock to XML with proper escaping."""
        # Use quoteattr for attributes to handle quotes and special chars safely
        # Use xml_escape for content to handle <, >, & characters
        path_attr = f" path={quoteattr(self.path)}" if self.path else ""
        return f"<codeblock lang={quoteattr(self.lang)}{path_attr}>\n{xml_escape(self.content)}\n</codeblock>"

    @classmethod
    @trace_function(name="codeblock.from_markdown", attributes={"component": "parser"})
    def from_markdown(cls, content: str) -> "Codeblock":
        content = content.replace("\r\n", "\n").replace("\r", "\n")
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

        # Slice the body on the raw first line length (including any leading
        # whitespace), not on the stripped lang: a fence like "``` python" leaves
        # a space before the lang, and slicing on len(lang) would leave the tail
        # of the lang word (e.g. the "n" of "python") leaking into the content.
        first_line = stripped.splitlines()[0] if stripped.strip() else ""
        lang = first_line.strip()
        fence = "`" * fence_len if fence_len else "```"
        return cls(
            lang,
            stripped[len(first_line) :].lstrip("\n") if lang else stripped,
            fence=fence,
        )

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
        # Strip leading/trailing newlines added by to_xml() formatting
        text = (root.text or "").strip("\n")
        return cls(
            root.attrib.get("lang", ""),
            text,
            root.attrib.get("path"),
        )

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


def _find_heredoc_terminator(line: str) -> str | None:
    """Return the heredoc terminator word if ``line`` opens a heredoc.

    Only recognizes ``<<`` operators at top-level (outside quoted strings,
    outside comments, and not backslash-escaped): a heredoc operator is
    shell syntax and never occurs inside a quoted literal or a ``#``
    comment, so ``echo "a << b"`` and ``# see a << b`` are correctly
    ignored while ``cat << 'EOF' > file.md`` is recognized. A backslash
    escapes the next character (matching shell quoting rules), so
    ``echo \\# not-a-comment`` and escaped quotes inside a double-quoted
    string don't desync the quote tracker. The terminator word accepts any
    run of non-whitespace, non-quote characters (bash heredoc words aren't
    restricted to ``\\w``, e.g. ``<<'END-TAG'``). Unquoted ``<<`` arithmetic
    shifts (e.g. ipython ``x << 2``) can still false-positive; the
    terminator word must then appear alone on a line to matter, which is
    rare in practice.
    """
    in_single = in_double = False
    i = 0
    n = len(line)
    while i < n - 1:
        c = line[i]
        if c == "\\" and not in_single:
            # Backslash escapes the next char outside single quotes (bash
            # doesn't allow escaping inside single quotes at all).
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif (
            c == "#"
            and not in_single
            and not in_double
            and (i == 0 or line[i - 1].isspace())
        ):
            # Start of a shell comment - nothing after it is executable syntax.
            return None
        elif (
            c == "<"
            and line[i + 1] == "<"
            and not in_single
            and not in_double
            and (i == 0 or line[i - 1] != "<")
        ):
            m = re.match(r"<<-?\s*['\"]?([^\s'\"]+)['\"]?", line[i:])
            if m:
                return m.group(1)
        i += 1
    return None


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
    # (since claude sometimes forgets to close codeblocks in its thinking,
    #  and gemini uses </thinking> instead of </think>)
    # Only strip when the closing tag is genuine: the corresponding opening tag
    # must be either (a) standalone at a line boundary, or (b) absent entirely
    # (Gemini uses "```thinking>" with no "<" before "thinking>", so no <thinking>
    # appears in the prefix at all).  We must NOT strip when <think> appears only
    # concatenated with a fence closer (e.g. "```<think>") — that is handled later
    # by the inner-loop fence-recovery logic.
    _concatenated_end_found = False
    _concatenated_end_pos = -1
    for _think_end_tag in ["</thinking>", "</think>"]:
        _think_end = markdown.find(_think_end_tag)
        if _think_end != -1:
            _think_start_tag = _think_end_tag.replace("/", "")  # e.g. "<think>"
            _prefix = markdown[:_think_end]
            _has_standalone = bool(
                re.search(r"(?:^|\n)" + re.escape(_think_start_tag), _prefix)
            )
            _has_any = _think_start_tag in _prefix
            # Strip if standalone opening exists, or if there is no opening tag at
            # all in the prefix (covers the Gemini "```thinking>" malformed case).
            if _has_standalone or not _has_any:
                # remove anything before and including the closing thinking tag
                markdown = markdown[_think_end + len(_think_end_tag) :]
                break
            # Found </think> but didn't strip: opening was concatenated (e.g. "```<think>").
            # Inner-loop fence-recovery handles this; track position of the closing tag.
            # NOTE: if both </thinking> and </think> appear concatenated in the same message,
            # _concatenated_end_pos is overwritten by the second iteration (the later position).
            # This means the _after_concat scan misses standalone think blocks between the two
            # concatenated end-tags. Emitting both concatenated closers in one message is
            # extremely unlikely in practice so this edge case is acceptable.
            _concatenated_end_found = True
            _concatenated_end_pos = _think_end + len(_think_end_tag)
    else:
        # if start thinking tag but no end, early exit (only for standalone tags;
        # concatenated occurrences like "```<think>" are handled by inner-loop logic).
        if not _concatenated_end_found:
            for _think_start_tag in ["<thinking>", "<think>"]:
                if re.search(r"(?:^|\n)" + re.escape(_think_start_tag), markdown):
                    return
        else:
            # A concatenated </think> was found (not stripped). Check if any standalone
            # <think> appearing AFTER that closing tag is genuinely unclosed. If so,
            # early-exit to avoid extracting blocks from inside an unclosed thinking section.
            _after_concat = markdown[_concatenated_end_pos:]
            for _think_start_tag in ["<thinking>", "<think>"]:
                _standalone_match = re.search(
                    r"(?:^|\n)" + re.escape(_think_start_tag), _after_concat
                )
                if _standalone_match:
                    _close_tag = _think_start_tag.replace("<", "</")
                    _rest = _after_concat[_standalone_match.start() :]
                    if _close_tag not in _rest:
                        return  # genuinely unclosed standalone think block

    # speed check (early exit): check if message contains a code block
    # Check for at least 2 fence markers (3+ backticks each)
    fence_pattern = re.compile(r"`{3,}")
    if len(fence_pattern.findall(markdown)) < 2:
        return

    # Normalize line endings so CRLF input does not leak carriage returns into content.
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")

    # Languages where a bare fence at depth 1 is never a nested opener.
    # Shell/ipython have no triple-backtick syntax; the look-ahead heuristic
    # only produces false positives (swallowing prose + output blocks into
    # the command). See gptme/gptme#3697.
    _EXEC_LANGS = frozenset(
        {
            "shell",
            "sh",
            "bash",
            "zsh",
            "fish",
            "ksh",
            "nu",
            "ps1",
            "powershell",
            "ipython",
        }
    )

    lines = markdown.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Recover from malformed adjacent outer fences where a block closes and the
        # next one opens on the same line, e.g. "``````patch file.py" instead of
        # "```\n```patch file.py". ToolUse._to_markdown always emits triple fences,
        # so six leading backticks followed immediately by a language tag is a good
        # signal that the first three backticks belong to the previous block.
        if re.match(r"^`{6}[^`\s]", line):
            line = line[3:]
            lines[i] = line

        # Look for code block start (3+ backticks)
        # Count the backticks at the start of the line
        fence_match = re.match(r"^(`{3,})", line)
        if fence_match:
            fence_len = len(fence_match.group(1))
            start_line = i  # Track the starting line number
            lang = line[fence_len:].strip()
            content_lines: list[str] = []
            i += 1
            reprocess_current_line = False

            # Track nesting depth to handle nested code blocks
            nesting_depth = 1
            # For exec langs: track heredoc state so embedded fences inside a heredoc
            # body (e.g. a shell script writing a markdown file) are treated as literal
            # content and never as block closers.
            heredoc_terminator: str | None = None

            # Collect content until we find the matching closing ```
            while i < len(lines):
                line = lines[i]

                # Recover from malformed adjacent fences where a closing fence for the
                # current block is directly concatenated with the opening fence of the
                # next block, e.g. "``````shell" instead of "```\n```shell".
                if nesting_depth == 1 and line.startswith("`" * fence_len):
                    rest = line[fence_len:]
                    # Only treat as adjacent fences when the remainder starts a new fence
                    # followed immediately by a non-whitespace character (e.g. a language
                    # tag like "shell"). The `{3,}` quantifier is greedy: it consumes all
                    # leading backticks in `rest`, so if `rest` consists solely of backticks
                    # (e.g. rest="```" from a content line like "``````") `\S` has no char
                    # left to match and the guard correctly falls through. Only when the
                    # backtick run is followed by a non-whitespace char (a language tag or
                    # similar) does this match — preventing false splits on bare-backtick
                    # content lines.
                    if re.match(r"^`{3,}\S", rest):
                        yield Codeblock(
                            lang,
                            "\n".join(content_lines),
                            start=start_line,
                            fence="`" * fence_len,
                        )
                        lines[i] = rest
                        reprocess_current_line = True
                        break

                    # Some models concatenate the closing fence with a thinking tag,
                    # e.g. "```<think>". Recover by treating the leading fence as the
                    # closing delimiter and reprocessing the remainder on the same line.
                    if rest.startswith(("<think>", "<thinking>")):
                        yield Codeblock(
                            lang,
                            "\n".join(content_lines),
                            start=start_line,
                            fence="`" * fence_len,
                        )
                        lines[i] = rest
                        reprocess_current_line = True
                        break

                # Update heredoc state for exec langs before fence detection.
                # A heredoc body may contain literal fence lines (e.g. a shell script
                # writing a markdown file via cat << EOF).  Track open/close so the
                # fence-termination logic below can distinguish them from block closers.
                if lang in _EXEC_LANGS:
                    if heredoc_terminator is None:
                        heredoc_terminator = _find_heredoc_terminator(line)
                    elif line.strip() == heredoc_terminator:
                        heredoc_terminator = None

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
                        # In streaming mode, a trailing empty string from split("\n")
                        # is indistinguishable from a real blank line. When the blank
                        # line is the last element, it's likely a split artifact from
                        # content ending with "\n", not a real confirmation line.
                        # Only treat it as a real blank if there's more content after.
                        if streaming and next_is_blank and i + 1 == len(lines) - 1:
                            next_is_blank = False
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
                                    lang,
                                    "\n".join(content_lines),
                                    start=start_line,
                                    fence="`" * fence_len,
                                )
                                i += 1
                                break
                            else:
                                content_lines.append(line)
                        elif (
                            next_has_content
                            and not next_is_fence
                            and (
                                lang not in _EXEC_LANGS
                                or heredoc_terminator is not None
                            )
                        ):
                            # Next line has content - check if this is a real nested block.
                            # For exec langs outside a heredoc: bare fence is always a
                            # closer (no triple-backtick syntax).  Inside a heredoc the
                            # fence is literal content, so apply the look-ahead as normal.
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
                                elif streaming:
                                    # In streaming mode, be conservative: treat bare fence
                                    # followed by content as a nested block opener even without
                                    # confirmed closing pattern. This prevents incorrectly
                                    # extracting incomplete blocks when later bare fences
                                    # would close this nested block but leave the outer unclosed.
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
                                        lang,
                                        "\n".join(content_lines),
                                        start=start_line,
                                        fence="`" * fence_len,
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
                                    lang,
                                    "\n".join(content_lines),
                                    start=start_line,
                                    fence="`" * fence_len,
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
            if reprocess_current_line:
                continue
        else:
            i += 1
