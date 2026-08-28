"""Snapshot-anchored file editing inspired by oh-my-pi's Hashline format.

Provides the ``hashline_edit`` tool: a two-call editing cycle where the
``read`` tool populates a snapshot store with a file-level 4-byte SHA-256 tag,
and ``hashline_edit`` verifies that tag before applying line-range operations.

Workflow
--------
1. ``read <path>`` — returns the file with a ``[PATH#TAG]`` header on the first
   line of the code block body.  The snapshot is stored in-memory.
2. ``hashline_edit <path>`` — the code block body must begin with
   ``[PATH#TAG]`` matching the tag seen in step 1, then list one or more
   ``PUT``/``CUT`` operations.

Edit syntax
-----------
::

    [PATH#TAG]
    PUT N.=M:       — replace lines N through M (inclusive) with new content
    +new line 1
    +new line 2
    PUT <N:         — insert the new content BEFORE line N
    +new line
    PUT >N:         — insert the new content AFTER line N
    +new line
    PUT N*:         — replace the syntactic block starting at line N
    +new content
    CUT N.=M        — delete lines N through M (no content block follows)
    CUT N.=M @r    — delete and save to named register r
    PUT >N @r       — paste register r content AFTER line N (no content block)
    PUT <N @r       — paste register r content BEFORE line N (no content block)
    PUT N.=M: @r    — replace lines N–M with register r content (no content block)

Content lines are prefixed with ``+``.  An operation's content block ends at
the next operation header or end-of-input.  Register-paste operations have no
content block.

If the live file's hash no longer matches the stored snapshot tag the entire
edit is rejected with a clear error — no silent corruption.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..message import Message
from ..util.git_cmd import GIT_CMD
from ._hashline_snapshot import lookup_snapshot, store_snapshot
from .base import Parameter, ToolSpec, ToolUse

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Operation dataclasses
# ---------------------------------------------------------------------------

OperationKind = Literal[
    "replace", "insert_before", "insert_after", "delete", "block_replace"
]


@dataclass
class HashlineOp:
    """A single parsed edit operation."""

    kind: OperationKind
    start: int  # 1-indexed, inclusive
    end: int  # 1-indexed, inclusive (== start for insert ops)
    text: str | None  # None for delete or for register-read ops (resolved before apply)
    register_name: str | None = None  # for CUT @name (capture) or PUT @name (paste)
    resolved_register_lines: list[str] | None = None


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# PUT N.=M:   replace lines N–M
_RE_PUT_RANGE = re.compile(r"^PUT\s+(\d+)\.=(\d+):\s*$")
# PUT N*:     replace the syntactic block starting at line N
_RE_PUT_BLOCK = re.compile(r"^PUT\s+(\d+)\*:\s*$")
# PUT <N:     insert before line N
_RE_PUT_BEFORE = re.compile(r"^PUT\s+<(\d+):\s*$")
# PUT >N:     insert after line N
_RE_PUT_AFTER = re.compile(r"^PUT\s+>(\d+):\s*$")
# CUT N.=M    delete lines N–M (no colon, no content block)
_RE_CUT_RANGE = re.compile(r"^CUT\s+(\d+)\.=(\d+)\s*$")
# CUT N.=M @name  delete lines N–M and save to named register
_RE_CUT_RANGE_REG = re.compile(r"^CUT\s+(\d+)\.=(\d+)\s+@(\w+)\s*$")
# PUT <N @name    insert register @name content before line N (no inline content)
_RE_PUT_BEFORE_REG = re.compile(r"^PUT\s+<(\d+)\s+@(\w+)\s*$")
# PUT >N @name    insert register @name content after line N (no inline content)
_RE_PUT_AFTER_REG = re.compile(r"^PUT\s+>(\d+)\s+@(\w+)\s*$")
# PUT N.=M: @name replace lines N–M with register @name content (no inline content)
_RE_PUT_RANGE_REG = re.compile(r"^PUT\s+(\d+)\.=(\d+):\s+@(\w+)\s*$")
# Header line [PATH#TAG]
_RE_HEADER = re.compile(r"^\[(.+)#([0-9A-Fa-f]{8})\]\s*$")


class ParseError(Exception):
    pass


def _parse_operations(code: str) -> tuple[str, str, list[HashlineOp]]:
    """Parse a hashline_edit code block.

    Returns (path, tag, operations).

    Raises :class:`ParseError` on any syntax problem.
    """
    lines = code.splitlines()
    if not lines:
        raise ParseError("Empty edit block")

    # First line must be [PATH#TAG]
    m = _RE_HEADER.match(lines[0].strip())
    if not m:
        raise ParseError(
            f"First line must be a snapshot header like [path#A1B2C3D4], got: {lines[0]!r}"
        )
    path = m.group(1)
    tag = m.group(2).upper()

    ops: list[HashlineOp] = []
    i = 1
    while i < len(lines):
        line = lines[i]

        # Register-capture: CUT N.=M @name — delete and save to named register
        if m := _RE_CUT_RANGE_REG.match(line):
            start, end, reg = int(m.group(1)), int(m.group(2)), m.group(3)
            if start > end:
                raise ParseError(f"CUT range start {start} > end {end}")
            ops.append(
                HashlineOp(
                    kind="delete", start=start, end=end, text=None, register_name=reg
                )
            )
            i += 1
            continue

        if m := _RE_CUT_RANGE.match(line):
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                raise ParseError(f"CUT range start {start} > end {end}")
            ops.append(HashlineOp(kind="delete", start=start, end=end, text=None))
            i += 1
            continue

        # Register-paste: PUT N.=M: @name — replace with register content
        if m := _RE_PUT_RANGE_REG.match(line):
            start, end, reg = int(m.group(1)), int(m.group(2)), m.group(3)
            if start > end:
                raise ParseError(f"PUT range start {start} > end {end}")
            ops.append(
                HashlineOp(
                    kind="replace", start=start, end=end, text=None, register_name=reg
                )
            )
            i += 1
            continue

        if m := _RE_PUT_RANGE.match(line):
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                raise ParseError(f"PUT range start {start} > end {end}")
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="replace", start=start, end=end, text=text))
            continue

        # Register-paste: PUT <N @name — insert register content before line N
        if m := _RE_PUT_BEFORE_REG.match(line):
            n, reg = int(m.group(1)), m.group(2)
            ops.append(
                HashlineOp(
                    kind="insert_before", start=n, end=n, text=None, register_name=reg
                )
            )
            i += 1
            continue

        if m := _RE_PUT_BEFORE.match(line):
            n = int(m.group(1))
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="insert_before", start=n, end=n, text=text))
            continue

        # Register-paste: PUT >N @name — insert register content after line N
        if m := _RE_PUT_AFTER_REG.match(line):
            n, reg = int(m.group(1)), m.group(2)
            ops.append(
                HashlineOp(
                    kind="insert_after", start=n, end=n, text=None, register_name=reg
                )
            )
            i += 1
            continue

        if m := _RE_PUT_AFTER.match(line):
            n = int(m.group(1))
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="insert_after", start=n, end=n, text=text))
            continue

        if m := _RE_PUT_BLOCK.match(line):
            n = int(m.group(1))
            i, text = _collect_content(lines, i + 1)
            # end=-1 is a sentinel; resolved against live file in _apply_operations
            ops.append(HashlineOp(kind="block_replace", start=n, end=-1, text=text))
            continue

        if line.strip() == "" or line.strip().startswith("#"):
            i += 1
            continue

        raise ParseError(f"Unrecognized operation line: {line!r}")

    return path, tag, ops


def _is_op_header(line: str) -> bool:
    return bool(
        _RE_PUT_RANGE.match(line)
        or _RE_PUT_BLOCK.match(line)
        or _RE_PUT_BEFORE.match(line)
        or _RE_PUT_AFTER.match(line)
        or _RE_CUT_RANGE.match(line)
        or _RE_CUT_RANGE_REG.match(line)
        or _RE_PUT_BEFORE_REG.match(line)
        or _RE_PUT_AFTER_REG.match(line)
        or _RE_PUT_RANGE_REG.match(line)
    )


def _collect_content(lines: list[str], start_idx: int) -> tuple[int, str]:
    """Collect ``+``-prefixed content lines starting at *start_idx*.

    Returns (next_index, collected_text).
    """
    content_lines: list[str] = []
    i = start_idx
    while i < len(lines):
        line = lines[i]
        if line.startswith("+"):
            content_lines.append(line[1:])
            i += 1
        elif line.strip() == "":
            i += 1
            break
        elif _is_op_header(line):
            break
        else:
            raise ParseError(
                f"Expected '+'-prefixed content line or next operation, got: {line!r}"
            )
    return i, "\n".join(content_lines)


# ---------------------------------------------------------------------------
# Block resolution
# ---------------------------------------------------------------------------


_RE_CONTINUATION_CLAUSE = re.compile(r"^(elif|else|except|finally)\b")
_RE_PYTHON_COMPOUND_HEADER = re.compile(
    r"^(?:async\s+)?(?:def|class|if|for|while|with|try)\b"
)


def _scan_line(line: str, open_quote: str | None = None) -> tuple[int, str | None]:
    """Scan *line*, returning ``(net bracket depth, triple-quote left open)``.

    *open_quote* is the triple-quote delimiter (``'''`` or ``\"\"\"``) left open by
    a previous line, or ``None`` when the line starts outside a string.  The
    returned delimiter is the one still open at end of line, so callers can carry
    multi-line string state across lines instead of re-deciding per line.
    Brackets and ``#`` comments inside strings are ignored.
    """
    depth = 0
    triple: str | None = open_quote  # multi-line string; persists across lines
    single: str | None = None  # single-quoted string; cannot span lines
    escaped = False
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if triple is not None:
            if ch == "\\":
                escaped = True
                i += 1
            elif line.startswith(triple, i):
                triple = None
                i += 3
            else:
                i += 1
            continue
        if single is not None:
            if ch == "\\":
                escaped = True
            elif ch == single:
                single = None
            i += 1
            continue
        if ch == "#":
            break
        if ch in {"'", '"'}:
            if line.startswith(ch * 3, i):
                triple = ch * 3
                i += 3
            else:
                single = ch
                i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        i += 1
    return depth, triple


def _resolve_block_end(file_lines: list[str], start: int) -> int:
    """Return the 1-indexed last line of the syntactic block starting at *start*.

    Uses an indent-tracking heuristic: the block header is at *start*; the block
    body is the contiguous sequence of non-blank lines with strictly greater
    indentation.  The block ends at the last such line (blank lines inside the body
    are absorbed).  If no body follows, the block is just the header line itself.

    A multi-line header delimited by brackets or by a triple-quoted string is
    consumed before indentation scanning begins, and triple-quoted string state
    is carried across body lines so a left-aligned string body doesn't terminate
    the block early. A same-indentation continuation clause
    (``elif``/``else``/``except``/``finally``) is treated as part of the same compound statement: its header
    and body are absorbed too, so the resolved range covers the whole
    ``if``/``try`` statement rather than stopping at the first clause. A
    comment line does not end the block by itself — it is skipped while
    scanning for a following continuation clause, so a comment placed between
    e.g. an ``if`` body and its ``else`` doesn't strand the ``else`` outside
    the resolved range.

    Raises :class:`ValueError` when *start* is out of range, or when the block
    cannot be delimited because a bracket or triple-quoted string opened by the
    block is never closed before end of file.
    """
    total = len(file_lines)
    if start < 1 or start > total:
        raise ValueError(
            f"Block start line {start} out of range (file has {total} lines)"
        )

    header = file_lines[start - 1]
    header_indent = len(header) - len(header.lstrip())
    python_compound = bool(_RE_PYTHON_COMPOUND_HEADER.match(header.lstrip()))
    bracket_depth, open_quote = _scan_line(header)

    end = start
    i = start  # file_lines[start] is the line AFTER the header (0-indexed)
    while i < total:
        line = file_lines[i]
        if bracket_depth > 0 or open_quote is not None:
            # Inside a multi-line header or a multi-line string: consume the line
            # verbatim, indentation carries no meaning here.
            delta, open_quote = _scan_line(line, open_quote)
            bracket_depth += delta
            end = i + 1
            i += 1
            continue
        if not line.strip():
            i += 1
            continue  # blank lines are absorbed; decide at the next non-blank line
        indent = len(line) - len(line.lstrip())
        if indent > header_indent:
            end = i + 1  # 1-indexed
            # A body line may open a triple-quoted string whose content is
            # left-aligned; track it so those lines stay inside the block.
            _, open_quote = _scan_line(line)
            i += 1
        elif line.lstrip().startswith("#"):
            i += 1  # comments may separate continuation clauses
        elif (
            python_compound
            and indent == header_indent
            and _RE_CONTINUATION_CLAUSE.match(line.lstrip())
        ):
            end = i + 1  # absorb the continuation clause header itself
            i += 1
        else:
            break  # same or lower indent, not a continuation clause: block is complete

    if bracket_depth > 0 or open_quote is not None:
        # Never closed before EOF — without this guard the scan absorbs the rest
        # of the file and the replace would silently truncate it.
        unterminated = "bracket" if bracket_depth > 0 else "string"
        raise ValueError(
            f"Block at line {start} has an unterminated {unterminated} — cannot "
            "determine where it ends; use an explicit range (PUT N.=M:) instead"
        )

    return end


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_operations(content: str, ops: list[HashlineOp]) -> str:
    """Apply *ops* to *content*, resolving all line references before mutating.

    Line numbers reference the ORIGINAL content.  Operations are applied
    bottom-up so earlier line numbers remain valid after later insertions/deletions.

    Register operations are resolved in two passes:
    1. Pre-scan: capture lines for all ``CUT @name`` ops (using ORIGINAL lines).
    2. Resolve: attach the captured lines to matching register PUT operations.

    Raises :class:`ValueError` on out-of-range line references or a missing register.
    """
    had_trailing_newline = content.endswith("\n")
    file_lines = content.splitlines()
    total = len(file_lines)

    # Resolve block_replace ops to concrete replace ranges before validation/sorting
    step1: list[HashlineOp] = []
    for op in ops:
        if op.kind == "block_replace":
            end = _resolve_block_end(file_lines, op.start)
            step1.append(
                HashlineOp(kind="replace", start=op.start, end=end, text=op.text)
            )
        else:
            step1.append(op)
    ops = step1

    # Pre-scan: capture register content from CUT @name ops (original line numbers)
    registers: dict[str, list[str]] = {}
    register_sources: dict[str, HashlineOp] = {}
    for op in ops:
        if op.kind == "delete" and op.register_name:
            if op.register_name in registers:
                raise ValueError(
                    f"Register @{op.register_name} is captured more than once; "
                    "use a unique register name for each CUT"
                )
            s, e = op.start - 1, op.end  # 0-indexed slice
            registers[op.register_name] = file_lines[s:e]
            register_sources[op.register_name] = op

    # Resolve register-read ops by attaching the original captured line list.
    step2: list[HashlineOp] = []
    for op in ops:
        if op.register_name and op.text is None and op.kind != "delete":
            reg = op.register_name
            if reg not in registers:
                raise ValueError(
                    f"Register @{reg} is not defined — "
                    "add a 'CUT N.=M @{reg}' operation earlier in the same edit block"
                )
            source = register_sources[reg]
            if op.start <= source.end and source.start <= op.end:
                raise ValueError(
                    f"Register @{reg} PUT lines {op.start}-{op.end} overlap "
                    f"its CUT lines {source.start}-{source.end}; use a destination "
                    "outside the captured range"
                )
            step2.append(
                HashlineOp(
                    kind=op.kind,
                    start=op.start,
                    end=op.end,
                    text=None,
                    resolved_register_lines=registers[reg],
                )
            )
        else:
            step2.append(op)
    ops = step2

    # A register paste that shares its start coordinate with another operation
    # has order-dependent semantics on the mutable line list. Preserve the
    # existing behavior for same-coordinate ordinary edits.
    for register_put in (op for op in ops if op.resolved_register_lines is not None):
        if any(
            other is not register_put and other.start == register_put.start
            for other in ops
        ):
            raise ValueError(
                f"Multiple operations start at line {register_put.start}; "
                "use distinct destination coordinates"
            )

    # A register paste destination must survive every other mutation, not just
    # the CUT that supplied its content. Otherwise bottom-up application can
    # insert content inside a range that a later operation deletes or replaces.
    for original_op, resolved_op in zip(step1, ops, strict=True):
        if not (
            original_op.register_name
            and original_op.text is None
            and original_op.kind != "delete"
        ):
            continue
        for other in ops:
            if other is resolved_op:
                continue
            if resolved_op.start <= other.end and other.start <= resolved_op.end:
                raise ValueError(
                    f"Register @{original_op.register_name} PUT lines "
                    f"{resolved_op.start}-{resolved_op.end} overlap "
                    f"{other.kind} lines {other.start}-{other.end}; use a "
                    "destination outside other mutation ranges"
                )

    for op in ops:
        # Allow PUT <1 on empty files
        if total == 0 and op.kind == "insert_before" and op.start == 1:
            continue
        if op.start < 1 or op.start > total:
            raise ValueError(f"Line {op.start} out of range (file has {total} lines)")
        if op.end < 1 or op.end > total:
            raise ValueError(f"Line {op.end} out of range (file has {total} lines)")

    # Apply bottom-up (highest line numbers first) to keep earlier indices stable
    for op in sorted(ops, key=lambda o: o.start, reverse=True):
        s = op.start - 1  # convert to 0-indexed
        e = op.end  # exclusive end for slicing

        if op.kind == "delete":
            del file_lines[s:e]
            continue

        new_lines = (
            op.resolved_register_lines
            if op.resolved_register_lines is not None
            else op.text.splitlines()
            if op.text
            else []
        )
        if op.kind == "replace":
            file_lines[s:e] = new_lines
        elif op.kind == "insert_before":
            file_lines[s:s] = new_lines
        elif op.kind == "insert_after":
            file_lines[e:e] = new_lines

    result = "\n".join(file_lines)
    if had_trailing_newline:
        result += "\n"
    return result


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

instructions = """
Use after ``read`` to apply precise, safe line edits without re-reading the full file.
The tag from ``read`` anchors your edits to the exact version you saw — any external
change to the file on disk is detected and rejected before a single byte is written.

Prefer this tool over ``patch`` when you have exact line numbers and want to avoid
repeating large context blocks.  You can chain multiple operations in one call;
line numbers always reference the version ``read`` showed you.

### Workflow

1. Call ``read <path>`` — the output starts with ``[PATH#TAG]`` on the first line
   inside the code block.  Remember this TAG.
2. Write a ``hashline_edit <path>`` block beginning with the same ``[PATH#TAG]``
   header, followed by one or more operations.

### Recovery from rejection

If the file changed between ``read`` and your edit, you will see:
  ``hashline_edit: file has changed since snapshot was captured…``
Re-read the file with ``read`` to get a new tag and restate your operations.

### Operations

| Syntax            | Effect                                           |
|-------------------|--------------------------------------------------|
| ``PUT N.=M:``     | Replace lines N through M with new lines         |
| ``PUT N*:``       | Replace the syntactic block at line N            |
| ``PUT <N:``       | Insert new lines BEFORE line N                   |
| ``PUT >N:``       | Insert new lines AFTER line N                    |
| ``CUT N.=M``      | Delete lines N through M                         |
| ``CUT N.=M @r``   | Delete lines N–M and save to register *r*        |
| ``PUT N.=M: @r``  | Replace lines N–M with content of register *r*   |
| ``PUT <N @r``     | Insert register *r* content BEFORE line N        |
| ``PUT >N @r``     | Insert register *r* content AFTER line N         |

``PUT N*:`` replaces a whole indented construct — point it at the ``def``,
``class``, ``if``, ``for``, ``while``, or ``try`` line and the system finds the
closing line for you, including any ``elif``/``else``/``except``/``finally``
clauses attached to that same statement.  Use it instead of ``PUT N.=M:`` when
you want to replace an entire function/block and don't want to count lines by
hand.

Point N at the actual header line (e.g. the ``def`` line, not a decorator or a
comment above it) — the block is resolved from that line's indentation.
Constructs the heuristic can't reliably reproduce: blocks that mix tabs and
spaces, or a header followed by a same-indentation line that is *not* a
continuation clause (e.g. a one-line ``if x: y`` body) — for those, fall back to
``PUT N.=M:`` with an explicit line range.

**Registers** avoid reproducing unchanged content when moving or copying exact
lines within one edit call, reducing tokens and transcription mistakes.
``CUT N.=M @r`` captures deleted lines into register *r* (a short word), then
``PUT >N @r`` or ``PUT <N @r`` pastes them. Registers are resolved from the
original file state, so a cut at line 5 and a paste at line 20 work correctly
regardless of apply order. The PUT destination must be outside the CUT range.

New content lines are prefixed with ``+``.  An empty line ends a content block.
CUT (with or without a register) has no content block.  Register-paste operations
(``PUT ... @r``) also have no content block.  Line numbers reference the version
shown by ``read``.

### Example

```
[greet.py#A1B2C3D4]
PUT 2.=3:
+    print(f"Hi, {name}")
CUT 4.=4
PUT >5:
+    return name
```

### Register example (move a function to the bottom)

```
[module.py#A1B2C3D4]
CUT 3.=10 @fn
PUT >40 @fn
```
""".strip()

instructions_format = {
    "markdown": "Use a code block with language tag: `hashline_edit <path>`",
}


def examples(tool_format) -> str:
    edit_body = """\
[greet.py#A1B2C3D4]
PUT 2.=3:
+    print(f"Hi, {name}")
"""
    return f"""
> User: update greet.py to use an f-string
> Assistant: First, read the file:
{ToolUse("read", ["greet.py"], "").to_output(tool_format)}
> System: ```greet.py
> [greet.py#A1B2C3D4]
>    1\\tdef greet(name):
>    2\\t    msg = "Hello, " + name
>    3\\t    print(msg)
> ```
> Assistant:
{ToolUse("hashline_edit", ["greet.py"], edit_body).to_output(tool_format)}
> System: hashline_edit applied to `greet.py` (1 operation)
""".strip()


def _path_from_args(
    args: list[str] | None, kwargs: dict[str, str] | None
) -> Path | None:
    if args:
        return Path(" ".join(args)).expanduser()
    if kwargs and kwargs.get("path"):
        return Path(kwargs["path"]).expanduser()
    return None


def _try_3way_merge(
    snapshot_content: str,
    ops: list[HashlineOp],
    live_content: str,
) -> tuple[str, bool]:
    """3-way merge recovery when the live file changed since the snapshot.

    Applies *ops* to *snapshot_content* (our side), then merges the result with
    *live_content* using *snapshot_content* as the common ancestor (base).  This
    preserves both the model's intended edits and any concurrent external changes
    to the file.

    Returns ``(merged_text, had_conflicts)``.  On a clean merge
    ``had_conflicts`` is False and ``merged_text`` is ready to write.  When
    conflicts remain the text contains standard conflict markers and
    ``had_conflicts`` is True.

    Raises :class:`ValueError` if the edit cannot be applied to the snapshot
    (forwarded from :func:`_apply_operations`) or if ``git`` is unavailable.
    """
    # Apply the edit to the snapshot — this is "our" side of the merge.
    ours = _apply_operations(snapshot_content, ops)

    # Write three temp files expected by git merge-file.
    # Paths are initialised to None so the finally always has valid names to
    # attempt unlinking — even if a write raises before the name is captured.
    ours_path = base_path = theirs_path = None
    try:
        with (
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".ours", delete=False, encoding="utf-8"
            ) as f_ours,
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".base", delete=False, encoding="utf-8"
            ) as f_base,
            tempfile.NamedTemporaryFile(
                mode="w", suffix=".theirs", delete=False, encoding="utf-8"
            ) as f_theirs,
        ):
            # Capture paths before writes so the finally block can clean up even
            # if a write raises (e.g. OSError: disk full, UnicodeEncodeError).
            ours_path = f_ours.name
            base_path = f_base.name
            theirs_path = f_theirs.name
            f_ours.write(ours)
            f_base.write(snapshot_content)
            f_theirs.write(live_content)

        # -p sends output to stdout; return code 0 = no conflicts, 1-127 = conflict
        # count.  Codes > 127 (e.g. 128/255) indicate git operational errors.
        result = subprocess.run(
            [
                GIT_CMD,
                "merge-file",
                "-p",
                "-L",
                "your edit (via hashline_edit)",
                "-L",
                "original snapshot",
                "-L",
                "current file",
                ours_path,
                base_path,
                theirs_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        had_conflicts = 0 < result.returncode <= 127
        if (
            result.returncode < 0
            or result.returncode > 127
            or (result.returncode != 0 and not result.stdout.strip())
        ):
            # returncode < 0   → process killed by signal (OOM, SIGKILL, etc.)
            # returncode > 127 → git operational error.
            # returncode 1-127 with empty stdout → nothing to show, treat as error.
            # returncode 1-127 with non-empty stdout → real conflict (markers present);
            # git may write warnings to stderr even for genuine conflicts, so
            # stderr presence alone is NOT a reliable indicator of operational failure.
            raise ValueError(
                f"git merge-file failed (exit {result.returncode}): "
                + (result.stderr.strip() or "no diagnostic available")
            )
        return result.stdout, had_conflicts
    except FileNotFoundError as e:
        raise ValueError(
            "git not found — cannot attempt 3-way merge recovery; "
            "call `read` again to get a fresh snapshot"
        ) from e
    except OSError as e:
        raise ValueError(f"3-way merge failed: {e}") from e
    finally:
        for p in [ours_path, base_path, theirs_path]:
            if p is not None:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass  # best-effort cleanup; never let cleanup suppress the real result


def execute_hashline_edit(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    path = _path_from_args(args, kwargs)
    if path is None:
        yield Message("system", "hashline_edit: no path provided")
        return

    body = (code or "").strip()
    if not body:
        yield Message("system", "hashline_edit: empty edit block")
        return

    # Parse the edit block
    try:
        _parsed_path, tag, ops = _parse_operations(body)
    except ParseError as e:
        yield Message("system", f"hashline_edit parse error: {e}")
        return

    if not ops:
        yield Message("system", "hashline_edit: no operations found in edit block")
        return

    # Resolve and read the actual file
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        yield Message("system", f"hashline_edit: file not found: {resolved}")
        return
    if not resolved.is_file():
        yield Message("system", f"hashline_edit: not a file: {resolved}")
        return

    try:
        live_content = resolved.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError) as e:
        yield Message("system", f"hashline_edit: cannot read file: {e}")
        return

    # Verify snapshot tag against the stored snapshot
    tag_matched, snapshot_content = lookup_snapshot(str(resolved), tag)
    if not tag_matched:
        # Check if we've seen the file at all (vs wrong tag)
        from ._hashline_snapshot import get_stored_tag

        stored = get_stored_tag(str(resolved))
        if stored is None:
            msg = (
                f"hashline_edit: no snapshot found for {resolved}. "
                "Call `read` first to capture a snapshot."
            )
        else:
            msg = (
                f"hashline_edit: tag mismatch for {resolved}. "
                f"Edit block has #{tag} but current snapshot is #{stored}. "
                "The file may have changed — call `read` again to get a fresh tag."
            )
        yield Message("system", msg)
        return

    # Verify the live file hasn't changed since the snapshot was captured.
    # Compare full content (not just truncated tag) to be collision-proof: a
    # 4-byte SHA-256 prefix could collide on different content, letting a stale
    # edit silently overwrite the wrong lines.
    assert snapshot_content is not None
    merge_recovered = False
    if live_content != snapshot_content:
        # Phase 2: attempt 3-way merge recovery so concurrent external changes
        # are preserved rather than forcing an unconditional re-read.
        # Limitation: merge is purely textual — semantic interactions between
        # the model's edit and an external change on different lines are not
        # detected.  The confirmation dialog below lets the user inspect the
        # merged preview before it is written; this is the primary mitigation.
        try:
            updated, had_conflicts = _try_3way_merge(
                snapshot_content, ops, live_content
            )
        except ValueError as e:
            yield Message("system", f"hashline_edit: {e}")
            return
        if had_conflicts:
            yield Message(
                "system",
                f"hashline_edit: file has changed since snapshot was captured for {resolved}. "
                "Automatic 3-way merge produced conflicts:\n\n"
                f"```\n{updated}\n```\n\n"
                "Resolve the conflicts manually, then call `read` to capture a fresh snapshot.",
            )
            return
        merge_recovered = True
    else:
        # Apply operations normally — file is unchanged since snapshot.
        try:
            updated = _apply_operations(live_content, ops)
        except ValueError as e:
            yield Message("system", f"hashline_edit: {e}")
            return

    # Ask for confirmation before writing (matches sibling tools' safety model)
    from ..hooks import ConfirmAction, get_confirmation

    confirm_result = get_confirmation(
        preview=updated,
        default_confirm=True,
    )
    if confirm_result.action == ConfirmAction.SKIP:
        yield Message(
            "system",
            confirm_result.message or "hashline_edit: operation cancelled by user",
        )
        return

    # Use content edited by the user during confirmation (if any)
    if (
        confirm_result.action == ConfirmAction.EDIT
        and confirm_result.edited_content is not None
    ):
        updated = confirm_result.edited_content

    # For merge-recovered edits, re-read the file right before writing to guard
    # against concurrent changes that arrived during the confirmation dialog.
    # This applies regardless of whether the user edited the proposed content —
    # the live file could have changed while the confirmation dialog was open.
    if merge_recovered:
        try:
            post_confirm_content = resolved.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            yield Message(
                "system", f"hashline_edit: cannot re-read file before write: {e}"
            )
            return
        if post_confirm_content != live_content:
            yield Message(
                "system",
                f"hashline_edit: file changed again during confirmation for {resolved}. "
                "Call `read` to get a fresh snapshot and retry.",
            )
            return

    # Write result atomically: write to a temp file in the same directory,
    # then rename into place.  os.replace() is a single POSIX syscall (rename(2))
    # so readers always see either the old or the new content — never a partial
    # write.  This closes the non-atomic write window noted by the AI reviewer
    # (the check→write TOCTOU window above is inherent to file I/O without
    # OS-level locking and is not specific to this path).
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=resolved.parent, prefix=".gptme_hashline_", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(updated)
        except Exception:
            os.unlink(tmp_name)
            raise
        os.replace(tmp_name, resolved)
    except (PermissionError, OSError) as e:
        yield Message("system", f"hashline_edit: write failed: {e}")
        return

    # Update snapshot for the new content
    store_snapshot(str(resolved), updated)

    n = len(ops)
    suffix = " (recovered via 3-way merge)" if merge_recovered else ""
    yield Message(
        "system",
        f"hashline_edit applied to `{resolved}` ({n} operation{'s' if n != 1 else ''}){suffix}",
    )


tool = ToolSpec(
    name="hashline_edit",
    desc="Apply snapshot-anchored line-range edits to a file (use after `read`)",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_hashline_edit,
    block_types=["hashline_edit"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="Path of the file to edit.",
            required=True,
        ),
        Parameter(
            name="edit",
            type="string",
            description=(
                "Edit block beginning with [PATH#TAG] then PUT/CUT operations. "
                "Content lines are prefixed with +."
            ),
            required=True,
        ),
    ],
    hints=frozenset({"file-ops", "destructive"}),
)

__doc__ = tool.get_doc(__doc__)
