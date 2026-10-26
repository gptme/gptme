import re
from bisect import bisect_left
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


def _find_heredoc_terminator(
    line: str,
    in_single: bool = False,
    in_double: bool = False,
    in_ansi_c: bool = False,
) -> tuple[str | None, bool, bool, bool]:
    """Return ``(terminator | None, in_single, in_double, in_ansi_c)``.

    The quote-state parameters let callers carry per-character quote state
    across lines so that a multiline single-quoted string (e.g.
    ``s='\\n<< EOF\\n'``) is not misidentified as a heredoc opener on the
    continuation line.  Returns the end-of-line quote state so callers can
    pass it into the next invocation.

    ``in_ansi_c`` tracks bash ANSI-C quoting (``$'...'``), where backslash
    escapes *are* processed (unlike POSIX single quotes).  Without this,
    ``x=$'a\\'b'`` is scanned as POSIX ``'...'`` and leaves ``in_single``
    set, so a later closing fence is treated as quoted content.

    Only recognizes ``<<`` operators at top-level (outside quoted strings,
    outside comments, and not backslash-escaped): a heredoc operator is
    shell syntax and never occurs inside a quoted literal or a ``#``
    comment, so ``echo "a << b"`` and ``# see a << b`` are correctly
    ignored while ``cat << 'EOF' > file.md`` is recognized. A backslash
    escapes the next character (matching shell quoting rules), so
    ``echo \\# not-a-comment`` and escaped quotes inside a double-quoted
    string don't desync the quote tracker. The terminator word accepts any
    run of non-whitespace, non-quote, non-backslash characters (bash
    heredoc words aren't restricted to ``\\w``, e.g. ``<<'END-TAG'``), with
    an optional single leading quote-or-backslash consumed but not part of
    the terminator (``<<'EOF'``, ``<<"EOF"``, and ``<<\\EOF`` all use the
    plain word ``EOF``). A ``<<<`` here-string takes one inline value and
    never opens a multi-line body, so it's explicitly not matched here -
    otherwise the capture group would swallow the third ``<`` as part of
    the (bogus) terminator and heredoc state would never close. A ``<<``
    inside ``$((...))`` arithmetic expansion is always a bit-shift, and a
    ``<<`` whose right operand is a bare integer literal is too, so both
    are rejected to avoid minting a phantom terminator that could swallow
    later fences.
    """
    i = 0
    n = len(line)
    # Iterate over ALL characters so that a quote at the very end of a line
    # (e.g. ``s='``) updates in_single/in_double for the caller to carry to the
    # next line.  The old ``while i < n - 1`` guard was safe for the ``<<``
    # look-ahead but silently skipped trailing quote characters.
    while i < n:
        c = line[i]
        if in_ansi_c:
            # ANSI-C $'...' : backslash escapes the next char, including quotes.
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_ansi_c = False
            i += 1
            continue
        if c == "\\" and not in_single:
            # Backslash escapes the next char outside single quotes (bash
            # doesn't allow escaping inside single quotes at all).
            i += 2
            continue
        if (
            c == "$"
            and not in_single
            and not in_double
            and i + 1 < n
            and line[i + 1] == "'"
        ):
            # Start of ANSI-C quoting: $'...'
            in_ansi_c = True
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
            return None, in_single, in_double, in_ansi_c
        elif (
            c == "$"
            and not in_single
            and not in_double
            and i + 2 < n
            and line[i + 1 : i + 3] == "(("
        ):
            # `$((...))` arithmetic expansion: `<<` inside is always a
            # bit-shift, never a heredoc. Skip to the matching `))` so a
            # shift like `$((1 << 3))` can't mint a phantom terminator.
            j = line.find("))", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        elif (
            c == "<"
            and i + 1 < n  # guard: need line[i+1] for the look-ahead
            and line[i + 1] == "<"
            and not in_single
            and not in_double
            and (i == 0 or line[i - 1] != "<")
        ):
            if i + 2 < n and line[i + 2] == "<":
                # `<<<` here-string, not a heredoc operator - skip past it.
                i += 2
                continue
            m = re.match(r"<<-?\s*['\"\\]?([^\s'\"\\]+)", line[i:])
            if m:
                term = m.group(1)
                if term.lstrip("+-").isdigit():
                    # `<< N` with an integer N is an arithmetic bit-shift
                    # (`x << 2`, `1 << 3`), not a heredoc opener. A bare
                    # integer heredoc delimiter is effectively never written
                    # in real shell, so reject it rather than mint a phantom
                    # terminator that could swallow later fences.
                    i += 2
                    continue
                # At the heredoc opener, quote state is always unquoted (the
                # guard above requires not in_single and not in_double).
                return term, False, False, False
        i += 1
    return None, in_single, in_double, in_ansi_c


def _line_ends_with_continuation(line: str) -> bool:
    """True if ``line`` ends with an unescaped backslash (Python line continuation)."""
    n = 0
    i = len(line) - 1
    while i >= 0 and line[i] == "\\":
        n += 1
        i -= 1
    return n % 2 == 1


def _scan_ipython_triple_quote(
    line: str,
    in_triple_double: bool,
    in_triple_single: bool,
    in_single: bool = False,
    in_double: bool = False,
) -> tuple[bool, bool, bool, bool]:
    """Scan one line of IPython content and return updated quote state.

    IPython embeds Python, which supports triple-single-quote and
    triple-double-quote strings spanning multiple lines.  A fence
    (bare `` ``` `` or language-tagged) inside such a string is literal
    Python source, not a markdown block delimiter.

    Ordinary ``'...'`` / ``"..."`` strings are tracked so
    ``value = '\"\"\"'`` does not open triple-double state and
    ``prefix = "#"; text = \"\"\"`` does not treat the ``#`` as a comment
    that hides the real opener.  Backslash escapes skip the next character
    inside any string.  ``#`` starts a comment only at top-level.

    Ordinary strings persist across a physical line break only when the
    line ends in an unescaped backslash (Python line continuation).  An
    unterminated quote without continuation is a syntax error and must
    not poison the next line — otherwise ``s = 'abc \\`` followed by
    ``\"\"\"`` falsely opens triple-double state.

    It does not attempt to resolve f-string expressions ``{...}`` or raw /
    bytes prefixes as a distinct mode.

    Returns ``(in_triple_double, in_triple_single, in_single, in_double)``
    after processing the line.
    """
    i = 0
    n = len(line)
    while i < n:
        c = line[i]

        # Inside a triple-quoted string: only look for closer and escapes.
        if in_triple_double or in_triple_single:
            if c == "\\":
                i += 2
                continue
            if in_triple_double and i + 2 < n and line[i : i + 3] == '"""':
                in_triple_double = False
                i += 3
                continue
            if in_triple_single and i + 2 < n and line[i : i + 3] == "'''":
                in_triple_single = False
                i += 3
                continue
            i += 1
            continue

        # Inside an ordinary quoted string: skip until closer. Triple-quote
        # characters and ``#`` are literal content here.
        if in_single:
            if c == "\\":
                i += 2
                continue
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue

        # Top-level: comments, string openers.
        if c == "#":
            break

        # Triple quotes before single (``"""`` is not ``"`` + ``""``).
        if i + 2 < n and line[i : i + 3] == '"""':
            in_triple_double = True
            i += 3
            continue
        if i + 2 < n and line[i : i + 3] == "'''":
            in_triple_single = True
            i += 3
            continue

        if c == "'":
            in_single = True
            i += 1
            continue
        if c == '"':
            in_double = True
            i += 1
            continue

        i += 1

    # Ordinary strings do not span lines unless this line is backslash-continued.
    if (in_single or in_double) and not _line_ends_with_continuation(line):
        in_single = False
        in_double = False

    return in_triple_double, in_triple_single, in_single, in_double


def _is_valid_lang_tag(potential_lang: str) -> bool:
    return bool(potential_lang) and (
        potential_lang[0].isalnum() or potential_lang[0] in "_/.~"
    )


def _is_thinking_lang(lang: str) -> bool:
    """True for thinking-section language tags, including Gemini's `thinking>`."""
    token = lang.strip().rstrip(">")
    first = token.split()[0] if token else ""
    return first in ("thinking", "think")


# Languages where a bare fence at depth 1 is never a nested opener.
# Shell/ipython have no triple-backtick syntax; the look-ahead heuristic
# only produces false positives (swallowing prose + output blocks into
# the command). See gptme/gptme#3697.
_EXEC_LANGS = frozenset(
    {"shell", "sh", "bash", "zsh", "fish", "ksh", "nu", "ps1", "powershell", "ipython"}
)
# Shell languages that support heredoc syntax (``<<``). IPython/Python's
# ``<<`` is always the bitwise left-shift operator — there is no heredoc
# syntax in Python — so ipython is excluded from heredoc state tracking.
_SHELL_LANGS = _EXEC_LANGS - {"ipython"}


def _line_fence_states(
    text: str, streaming: bool = False
) -> list[tuple[int, bool, str]]:
    """Per-line fence state matching `_extract_codeblocks` recovery rules.

    Each entry is `(content_depth, is_fence_delimiter, outer_lang)`:
    - `content_depth` is nesting depth of the line's payload (0 = outside any fence)
    - `is_fence_delimiter` is True for open/close/recovery lines (skip tag checks)
    - `outer_lang` is the current top-level fence language, or "" at depth 0

    Replicates adjacent-fence recovery (` ``` ```lang` / ` ``````lang`) and
    language-tagged nested openers so thinking-tag checks agree with extraction.
    """
    lines = text.split("\n")
    # Suffix queries only need to know whether a tagged fence remains. Compute
    # its last position once rather than rescanning for every literal example.
    last_tagged_fence = next(
        (
            i
            for i in range(len(lines) - 1, -1, -1)
            if re.match(r"^`{3,}", lines[i]) and len(lines[i].strip()) > 3
        ),
        -1,
    )
    # Index literal substring occurrences too: missing closers otherwise cause
    # a full suffix scan for every unclosed thinking-tag example.
    thinking_closer_lines = {
        tag: [i for i, line in enumerate(lines) if tag in line]
        for tag in ("</think>", "</thinking>")
    }
    # Prose can separate a completed tool fence from a reasoning opener.
    # Index both boundaries so recovery need not rescan every remaining suffix.
    thinking_opener_lines = [
        i for i, line in enumerate(lines) if line.startswith(("<think>", "<thinking>"))
    ]
    fence_lines = [i for i, line in enumerate(lines) if line.startswith("```")]
    states: list[tuple[int, bool, str]] = []
    depth = 0
    fence_len = 0
    outer_lang = ""
    ipy_td = ipy_ts = ipy_s = ipy_d = False
    qs_single = qs_double = qs_ansi = False
    heredoc_terminator: str | None = None
    for i, line in enumerate(lines):
        if depth == 0:
            scan = line[3:] if re.match(r"^`{6}[^`\s]", line) else line
            fence_match = re.match(r"^(`{3,})", scan)
            if fence_match:
                fence_len = len(fence_match.group(1))
                outer_lang = scan[fence_len:].strip()
                states.append((0, True, ""))
                depth = 1
                ipy_td = ipy_ts = ipy_s = ipy_d = False
                qs_single = qs_double = qs_ansi = False
                heredoc_terminator = None
                continue
            states.append((0, False, ""))
            continue

        if depth == 1 and line.startswith("`" * fence_len):
            rest = line[fence_len:]
            if re.match(r"^`{3,}\S", rest):
                rest_match = re.match(r"^(`{3,})", rest)
                if not rest_match:
                    states.append((1, False, outer_lang))
                    continue
                states.append((1, True, outer_lang))
                fence_len = len(rest_match.group(1))
                outer_lang = rest[fence_len:].strip()
                depth = 1
                ipy_td = ipy_ts = ipy_s = ipy_d = False
                qs_single = qs_double = qs_ansi = False
                heredoc_terminator = None
                continue
            if rest.startswith(("<think>", "<thinking>")):
                states.append((1, True, outer_lang))
                depth = 0
                fence_len = 0
                outer_lang = ""
                continue

        # Carry exactly the same shell/Python string state as extraction. In
        # particular, heredoc body quotes do not change the outer shell state.
        if outer_lang in _SHELL_LANGS:
            if heredoc_terminator is None:
                candidate, qs_single, qs_double, qs_ansi = _find_heredoc_terminator(
                    line, qs_single, qs_double, qs_ansi
                )
                if candidate is not None and any(
                    later.strip() == candidate for later in lines[i + 1 : i + 201]
                ):
                    heredoc_terminator = candidate
            elif line.strip() == heredoc_terminator:
                heredoc_terminator = None
                qs_single = qs_double = qs_ansi = False
        elif outer_lang == "ipython":
            ipy_td, ipy_ts, ipy_s, ipy_d = _scan_ipython_triple_quote(
                line, ipy_td, ipy_ts, ipy_s, ipy_d
            )

        line_fence_match = re.match(r"^(`{3,})", line)
        if line_fence_match:
            line_fence_len = len(line_fence_match.group(1))
            is_bare_fence = line.strip() == "`" * line_fence_len
            is_outer_close = is_bare_fence and line_fence_len == fence_len
            if is_outer_close or (is_bare_fence and depth > 1):
                if qs_single or qs_double or qs_ansi or ipy_td or ipy_ts:
                    states.append((depth, False, outer_lang))
                    continue
                next_has_content = i + 1 < len(lines) and bool(lines[i + 1].strip())
                next_is_fence = i + 1 < len(lines) and bool(
                    re.match(r"^`{3,}", lines[i + 1])
                )
                if (
                    depth == 1
                    and next_has_content
                    and not next_is_fence
                    and (
                        outer_lang not in _EXEC_LANGS or heredoc_terminator is not None
                    )
                ):
                    # Match extraction's bounded nested-fence lookahead. Without
                    # a confirmed inner closer, non-streaming keeps this fence
                    # literal; streaming conservatively opens another level.
                    has_closing_fence = False
                    has_following_tagged_fence = False
                    nested_closer_idx: int | None = None
                    for j in range(i + 1, min(i + 20, len(lines))):
                        if re.match(r"^(`{3,})$", lines[j].strip()):
                            nested_closer_idx = j
                            has_closing_fence = any(
                                lines[k].strip()
                                for k in range(j + 1, min(j + 5, len(lines)))
                            )
                            break
                        if re.match(r"^`{3,}", lines[j]) and len(lines[j].strip()) > 3:
                            has_following_tagged_fence = True
                            break
                    states.append((depth, True, outer_lang))
                    next_line = lines[i + 1]
                    opener_pos = bisect_left(thinking_opener_lines, i + 1)
                    fence_pos = bisect_left(fence_lines, i + 1)
                    if opener_pos < len(thinking_opener_lines):
                        opener_line = lines[thinking_opener_lines[opener_pos]]
                        opener_close = (
                            "</thinking>"
                            if opener_line.startswith("<thinking>")
                            else "</think>"
                        )
                        # Intervening output/example fences must not hide an
                        # unfinished reasoning opener from the fail-closed path.
                        if (
                            fence_pos == len(fence_lines)
                            or thinking_opener_lines[opener_pos]
                            < fence_lines[fence_pos]
                            or bisect_left(
                                thinking_closer_lines[opener_close],
                                thinking_opener_lines[opener_pos],
                            )
                            == len(thinking_closer_lines[opener_close])
                        ):
                            next_line = opener_line
                    if heredoc_terminator is None and next_line.startswith(
                        ("<think>", "<thinking>")
                    ):
                        # Heredoc payload tags are literal shell content, not
                        # evidence that the preceding tool fence has closed.
                        # Only this branch reads `nested_example_continues`.
                        # Scanning the remaining suffix on every bare fence
                        # made large Markdown quadratic even when no thinking
                        # tags were present.
                        nested_example_continues = False
                        if nested_closer_idx is not None:
                            # A later tagged fence after the nested closer is a
                            # tool call. That shape is also unfinished reasoning
                            # after a completed save (`Considering:` then ```draft
                            # then ```shell): unlabelled content between the tag
                            # and nested fence cannot tell those apart, so fail
                            # closed. Nested unclosed-think saves still extract
                            # when no later tagged command follows. Search the
                            # rest of the message, not a 4-line window: blank
                            # lines between the nested closer and a later
                            # ```shell would otherwise hide the command.
                            nested_example_continues = (
                                last_tagged_fence <= nested_closer_idx
                            )
                        start_tag = (
                            "<thinking>"
                            if next_line.startswith("<thinking>")
                            else "<think>"
                        )
                        close_tag = start_tag.replace("<", "</")
                        closer_lines = thinking_closer_lines[close_tag]
                        closer_pos = bisect_left(closer_lines, i + 1)
                        thinking_closer_idx = (
                            closer_lines[closer_pos]
                            if closer_pos < len(closer_lines)
                            else None
                        )
                        thinking_closed = thinking_closer_idx is not None
                        # A tagged fence can itself be inside a literal nested
                        # example. Confirm the unlabelled example's closer after
                        # the thinking span instead of truncating the outer save
                        # at its opener merely because lookahead hit ```shell.
                        if thinking_closer_idx is not None:
                            after_thinking = bisect_left(
                                fence_lines, thinking_closer_idx + 1
                            )
                            if after_thinking < len(fence_lines):
                                example_close = fence_lines[after_thinking]
                                if (
                                    re.fullmatch(r"`{3,}", lines[example_close])
                                    and example_close + 1 < len(lines)
                                    and lines[example_close + 1].strip()
                                    and not lines[example_close + 1].startswith("```")
                                ):
                                    has_closing_fence = True
                                    nested_example_continues = True
                        # A bare fence *before* the reasoning closer belongs
                        # to reasoning, not a literal example beginning with
                        # thinking tags. A later tagged tool confirms that the
                        # preceding save ended; otherwise keep nested examples.
                        reasoning_contains_fence = (
                            nested_closer_idx is not None
                            and thinking_closer_idx is not None
                            and nested_closer_idx < thinking_closer_idx
                        )
                        # Unclosed reasoning after this fence means the tool
                        # block completed — nested unlabelled examples close
                        # their thinking tags because those tags are content.
                        # An unclosed opener as the first nested-example line
                        # still continues the save when lookahead finds a
                        # nested closer and no later tagged command.
                        # During streaming, closed tags alone cannot distinguish
                        # reasoning from a still-growing literal nested example.
                        # Wait for a following tagged fence before recovering a
                        # completed tool block; never execute a truncated save.
                        if (
                            streaming
                            and thinking_closer_idx is not None
                            and has_following_tagged_fence
                            and fence_pos < len(fence_lines)
                            and fence_lines[fence_pos] < thinking_closer_idx
                            and not has_closing_fence
                        ):
                            # A growing literal example may not have emitted
                            # its closing fence yet. Do not expose a prefix save.
                            depth += 1
                        elif (
                            (not thinking_closed or reasoning_contains_fence)
                            and not nested_example_continues
                        ) or (
                            not has_closing_fence
                            and (not streaming or has_following_tagged_fence)
                        ):
                            depth = 0
                            fence_len = 0
                            outer_lang = ""
                        else:
                            depth += 1
                    elif has_closing_fence or streaming:
                        depth += 1
                    continue
                if (
                    depth == 1
                    and streaming
                    and (
                        outer_lang not in _EXEC_LANGS or heredoc_terminator is not None
                    )
                    and not (i + 1 < len(lines) - 1 and not lines[i + 1].strip())
                ):
                    states.append((depth, True, outer_lang))
                    depth += 1
                    continue
                states.append((depth, True, outer_lang))
                depth -= 1
                if depth == 0:
                    fence_len = 0
                    outer_lang = ""
                continue
            potential_lang = line[line_fence_len:].strip()
            if _is_valid_lang_tag(potential_lang) and not (ipy_td or ipy_ts):
                states.append((depth, True, outer_lang))
                depth += 1
                continue
            states.append((depth, False, outer_lang))
            continue

        states.append((depth, False, outer_lang))
    return states


def _is_standalone_outside_fences(text: str, tag: str, streaming: bool = False) -> bool:
    """
    Return True if `tag` appears at the start of a line in `text` that is NOT inside a code fence.

    Used to distinguish genuine thinking-section markers from the same tags appearing
    as literal file content inside a save/patch code block. Fence tracking follows
    `_extract_codeblocks` (adjacent-fence recovery and nested language tags).
    """
    if tag not in text:
        return False
    lines = text.split("\n")
    for line, (depth, is_delim, _) in zip(
        lines, _line_fence_states(text, streaming=streaming), strict=True
    ):
        if is_delim:
            continue
        if depth == 0 and line.startswith(tag):
            return True
    return False


def _literal_thinking_closers(text: str, streaming: bool = False) -> set[int]:
    """Offsets of closing thinking tags inside non-thinking code fences.

    Compute all offsets in one pass so repeated literal tags in templates do
    not each rescan the whole message during streaming extraction.

    A closer that appears after a genuine standalone thinking opener is still
    a thinking-section end even if a code fence opened *inside* that reasoning
    and was never closed (Claude sometimes forgets to close those). Skipping
    it would hide later tool blocks.
    """
    lines = text.split("\n")
    states = _line_fence_states(text, streaming=streaming)
    literal_closers: set[int] = set()
    offset = 0
    in_thinking = False
    for line, (depth, is_delim, outer_lang) in zip(lines, states, strict=True):
        if (
            (not is_delim and depth == 0) or (is_delim and line.startswith("```"))
        ) and line.lstrip("`").startswith(("<thinking>", "<think>")):
            in_thinking = True
        if not in_thinking and depth > 0 and not _is_thinking_lang(outer_lang):
            literal_closers.update(
                offset + match.start()
                for match in re.finditer(r"</(?:thinking|think)>", line)
            )
        if in_thinking and re.search(r"</(?:thinking|think)>", line):
            # Inline reasoning can open and close on the same line. A closer
            # also ends reasoning whose inner code fence was left unclosed.
            in_thinking = False
        offset += len(line) + 1
    return literal_closers


def _unclosed_standalone_after(
    text: str, after_pos: int, streaming: bool = False
) -> bool:
    """True if a fence-outside thinking opener after `after_pos` has no closer."""
    # The closer ends reasoning even when its inner fence was left unclosed.
    # Fence state from that reasoning must not hide openers in the new suffix.
    text = text[after_pos:]
    lines = text.split("\n")
    states = _line_fence_states(text, streaming=streaming)
    offset = 0
    for line, (depth, is_delim, _) in zip(lines, states, strict=True):
        line_start = offset
        offset += len(line) + 1
        if is_delim or depth != 0:
            continue
        for start_tag in ("<thinking>", "<think>"):
            if line.startswith(start_tag):
                close_tag = start_tag.replace("<", "</")
                if close_tag not in text[line_start:]:
                    return True
    return False


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
    # Normalize line endings first: the thinking-tag boundary checks below match
    # on "\n", so a CR-only "<think>" would otherwise escape the unclosed-tag exit
    # and normalization would then expose its fences as code blocks.
    markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")

    # dont extract codeblocks from thinking blocks
    # (since claude sometimes forgets to close codeblocks in its thinking,
    #  and gemini uses </thinking> instead of </think>)
    # Only strip when the closing tag is genuine: the corresponding opening tag
    # must be either (a) standalone at a line boundary, or (b) absent entirely
    # (Gemini uses "```thinking>" with no "<" before "thinking>", so no <thinking>
    # appears in the prefix at all).  We must NOT strip when <think> appears only
    # concatenated with a fence closer (e.g. "```<think>") — that is handled later
    # by the inner-loop fence-recovery logic.
    while True:
        _concatenated_end_found = False
        _concatenated_end_pos = -1
        _stripped = False
        _literal_closers = (
            _literal_thinking_closers(markdown, streaming=streaming)
            if "</think" in markdown
            else set()
        )
        for _think_end_tag in ["</thinking>", "</think>"]:
            _search_from = 0
            while True:
                _think_end = markdown.find(_think_end_tag, _search_from)
                if _think_end == -1:
                    break
                # Literal closer inside save/patch/shell/etc. is file content, not a
                # thinking-section end. Skip it and keep looking (Gemini `thinking>`
                # fences are still actionable — they are thinking langs).
                if _think_end in _literal_closers:
                    _search_from = _think_end + 1
                    continue
                _think_start_tag = _think_end_tag.replace("/", "")  # e.g. "<think>"
                # Use the complete message for fence lookahead, not a prefix
                # truncated just before this closer. Keep tool calls preceding
                # the opener when removing a later reasoning section.
                _standalone_start = None
                _concatenated_start = None
                _gemini_fence_start = None
                _has_any = False
                _offset = 0
                for _line, (_depth, _delim, _outer) in zip(
                    markdown.split("\n"),
                    _line_fence_states(markdown, streaming=streaming),
                    strict=True,
                ):
                    if _offset >= _think_end:
                        break
                    # Only text before this closer can open it. A second
                    # `<think>` after `</think>` on the same line is a new
                    # section, not an opener preceding this closer.
                    _line_before = _line[: max(0, _think_end - _offset)]
                    _payload = _line_before.lstrip("`")
                    _concat_here = _delim and _payload.startswith(_think_start_tag)
                    if _think_start_tag in _line_before and (
                        _depth == 0 or _is_thinking_lang(_outer) or _concat_here
                    ):
                        _has_any = True
                    if _concat_here and _concatenated_start is None:
                        # Keep the fence closer (` ``` `) and strip from the
                        # thinking tag. Leaving the span in place lets fence
                        # recovery split ```<think> and extract inner commands.
                        _concatenated_start = (
                            _offset + len(_line_before) - len(_payload)
                        )
                    if (
                        not _delim
                        and _depth == 0
                        and _line.startswith(_think_start_tag)
                    ):
                        if _standalone_start is None:
                            # Repeated openers are one span: keep the first.
                            _standalone_start = _offset
                    if _delim and _depth == 0:
                        _fence_match = re.match(r"^(`{3,})", _line)
                        if _fence_match and _is_thinking_lang(
                            _line[len(_fence_match.group(1)) :].strip()
                        ):
                            _gemini_fence_start = _offset
                    _offset += len(_line) + 1
                _has_standalone = _standalone_start is not None
                # Strip if standalone opening exists, concatenated ```<think>
                # is closed, or there is no real opening tag outside literal
                # file content (Gemini ```thinking>).
                if _has_standalone or not _has_any or _concatenated_start is not None:
                    # Ordinary reasoning removes only its own span. Concatenated
                    # ```<think> keeps the fence closer. Gemini's ```thinking>
                    # opener has no <thinking> tag; strip from that fence so a
                    # prior save with literal tags survives.
                    if _standalone_start is not None or _concatenated_start is not None:
                        # Repeated openers form one span regardless of whether
                        # the first opener was standalone or fence-concatenated.
                        _start = min(
                            pos
                            for pos in (_standalone_start, _concatenated_start)
                            if pos is not None
                        )
                    elif _gemini_fence_start is not None:
                        _start = _gemini_fence_start
                    else:
                        _start = 0
                    markdown = (
                        markdown[:_start]
                        + ("\n" if _start > 0 else "")
                        + markdown[_think_end + len(_think_end_tag) :]
                    )
                    _stripped = True
                    break
                # Found </think> but didn't strip: opening was inline in prose,
                # not a line-start or ```<think> fence. Inner-loop recovery
                # still needs the closer position for unclosed-after checks.
                # NOTE: if both </thinking> and </think> appear concatenated in the same message,
                # _concatenated_end_pos is overwritten by the second iteration (the later position).
                # This means the _after_concat scan misses standalone think blocks between the two
                # concatenated end-tags. Emitting both concatenated closers in one message is
                # extremely unlikely in practice so this edge case is acceptable.
                _concatenated_end_found = True
                _concatenated_end_pos = _think_end + len(_think_end_tag)
                break  # first actionable closer of this tag type
            if _stripped:
                break
        if not _stripped:
            break
        # Recheck both variants after stripping; offsets and fence state belong
        # to the remaining suffix, and it may contain another thinking section.

    # if start thinking tag but no end, early exit (only for standalone tags;
    # concatenated occurrences like "```<think>" are handled by inner-loop logic).
    if not _concatenated_end_found:
        for _think_start_tag in ["<thinking>", "<think>"]:
            if _is_standalone_outside_fences(
                markdown, _think_start_tag, streaming=streaming
            ):
                return
    else:
        # A concatenated </think> was found (not stripped). Check if any standalone
        # <think> appearing AFTER that closing tag is genuinely unclosed. If so,
        # early-exit to avoid extracting blocks from inside an unclosed thinking section.
        if _unclosed_standalone_after(
            markdown, _concatenated_end_pos, streaming=streaming
        ):
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
            # Cross-line quote state for the heredoc scanner: a shell single-quoted
            # string can span multiple lines (e.g. ``s='\n<< EOF\n'``).  Without
            # carrying in_single/in_double across lines, the ``<< EOF`` on the
            # continuation line is misidentified as a heredoc opener.
            _qs_in_single: bool = False
            _qs_in_double: bool = False
            _qs_in_ansi_c: bool = False
            # For IPython blocks: track whether we are inside a Python triple-quoted
            # string (``"""`` or ``'''``).  A bare fence inside such a string is
            # literal Python source, not a markdown block delimiter.
            # Ordinary quote flags persist only across backslash-continued lines
            # so ``s = 'abc \\`` + ``\"\"\"`` does not open triple-double state.
            _ipython_in_triple_double: bool = False
            _ipython_in_triple_single: bool = False
            _ipython_in_single: bool = False
            _ipython_in_double: bool = False

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
                #
                # A candidate terminator is only trusted once it's confirmed to appear
                # alone on a later line within a bounded window. Without this check, an
                # unrelated unquoted `<<` (e.g. an arithmetic shift `x << 2`, or ipython
                # `x << 2`) sets a phantom terminator that never matches anything, so
                # heredoc state would stay "open" for the rest of the message -
                # permanently disabling the bare-fence-is-a-closer rule and swallowing
                # every block that follows. The window can't stop at the next bare
                # fence: a real heredoc body legitimately contains fence-like lines
                # (that's the case this tracking exists to handle), so a fence-bounded
                # scan would break on the very heredocs it's meant to support. A wide
                # but finite window instead bounds both the false-confirm blast radius
                # (an unrelated standalone line matching the candidate far later in the
                # message) and the O(n^2) worst case on very large documents.
                if lang in _SHELL_LANGS:
                    if heredoc_terminator is None:
                        candidate, _qs_in_single, _qs_in_double, _qs_in_ansi_c = (
                            _find_heredoc_terminator(
                                line, _qs_in_single, _qs_in_double, _qs_in_ansi_c
                            )
                        )
                        _confirm_window = lines[i + 1 : i + 1 + 200]
                        if candidate is not None and any(
                            later.strip() == candidate for later in _confirm_window
                        ):
                            heredoc_terminator = candidate
                    elif line.strip() == heredoc_terminator:
                        heredoc_terminator = None
                        # Heredoc body is verbatim text — it doesn't affect the
                        # outer shell's quote state.  A heredoc opener always
                        # appears outside any quoted string (the not-in_single /
                        # not-in_double guards ensure this), so restoring to
                        # unquoted after the body closes is always correct.
                        _qs_in_single = False
                        _qs_in_double = False
                        _qs_in_ansi_c = False
                elif lang == "ipython":
                    # Update triple-quoted-string state line-by-line so that
                    # bare fences inside ``"""..."""`` or ``'''...'''`` are
                    # treated as literal Python content rather than block closers.
                    (
                        _ipython_in_triple_double,
                        _ipython_in_triple_single,
                        _ipython_in_single,
                        _ipython_in_double,
                    ) = _scan_ipython_triple_quote(
                        line,
                        _ipython_in_triple_double,
                        _ipython_in_triple_single,
                        _ipython_in_single,
                        _ipython_in_double,
                    )

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
                        # A fence inside an open quoted string or a Python
                        # triple-quoted string is literal content, not a markdown
                        # delimiter (same rationale as heredoc bodies).
                        if (
                            _qs_in_single
                            or _qs_in_double
                            or _qs_in_ansi_c
                            or _ipython_in_triple_double
                            or _ipython_in_triple_single
                        ):
                            content_lines.append(line)
                            i += 1
                            continue

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

                                # Genuine reasoning was removed above. Any tags
                                # still here belong to literal content and must
                                # not confirm closure of a growing nested save.
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
                            # Exec langs (shell, bash, ipython, …) have no
                            # triple-backtick syntax of their own, so a bare
                            # fence at depth 1 is unambiguously a block closer
                            # even in streaming mode — no blank-line confirmation
                            # is required.  This makes streaming extraction
                            # consistent with non-streaming for exec langs and
                            # prevents the trailing-blank-line sensitivity where
                            # an extra ``\n`` at end-of-message flips the result
                            # from no-block to an oversized block.
                            if (
                                lang in _EXEC_LANGS
                                and heredoc_terminator is None
                                and not _qs_in_single
                                and not _qs_in_double
                                and not _qs_in_ansi_c
                                and not _ipython_in_triple_double
                                and not _ipython_in_triple_single
                            ):
                                yield Codeblock(
                                    lang,
                                    "\n".join(content_lines),
                                    start=start_line,
                                    fence="`" * fence_len,
                                )
                                i += 1
                                break
                            # For non-exec langs: require blank line to confirm
                            # closure (a bare fence could be the start of a
                            # nested block that isn't finished yet).
                            elif next_is_blank:
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
                        # Language-tagged fence. Inside a Python triple-quoted
                        # string this is literal source, not a nested markdown
                        # opener — incrementing nesting_depth here would leave
                        # the depth elevated because the matching bare closer
                        # is also treated as literal (see gptme/gptme#3773).
                        if _ipython_in_triple_double or _ipython_in_triple_single:
                            content_lines.append(line)
                            i += 1
                            continue
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
