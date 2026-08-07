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
    CUT N.=M        — delete lines N through M (no content block follows)

Content lines are prefixed with ``+``.  An operation's content block ends at
the next operation header or end-of-input.

If the live file's hash no longer matches the stored snapshot tag the entire
edit is rejected with a clear error — no silent corruption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..message import Message
from ._hashline_snapshot import lookup_snapshot, store_snapshot
from .base import Parameter, ToolSpec, ToolUse

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Operation dataclasses
# ---------------------------------------------------------------------------

OperationKind = Literal["replace", "insert_before", "insert_after", "delete"]


@dataclass
class HashlineOp:
    """A single parsed edit operation."""

    kind: OperationKind
    start: int  # 1-indexed, inclusive
    end: int  # 1-indexed, inclusive (== start for insert ops)
    text: str | None  # None for delete


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# PUT N.=M:   replace lines N–M
_RE_PUT_RANGE = re.compile(r"^PUT\s+(\d+)\.=(\d+):\s*$")
# PUT <N:     insert before line N
_RE_PUT_BEFORE = re.compile(r"^PUT\s+<(\d+):\s*$")
# PUT >N:     insert after line N
_RE_PUT_AFTER = re.compile(r"^PUT\s+>(\d+):\s*$")
# CUT N.=M    delete lines N–M (no colon, no content block)
_RE_CUT_RANGE = re.compile(r"^CUT\s+(\d+)\.=(\d+)\s*$")
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

        if m := _RE_CUT_RANGE.match(line):
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                raise ParseError(f"CUT range start {start} > end {end}")
            ops.append(HashlineOp(kind="delete", start=start, end=end, text=None))
            i += 1
            continue

        if m := _RE_PUT_RANGE.match(line):
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                raise ParseError(f"PUT range start {start} > end {end}")
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="replace", start=start, end=end, text=text))
            continue

        if m := _RE_PUT_BEFORE.match(line):
            n = int(m.group(1))
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="insert_before", start=n, end=n, text=text))
            continue

        if m := _RE_PUT_AFTER.match(line):
            n = int(m.group(1))
            i, text = _collect_content(lines, i + 1)
            ops.append(HashlineOp(kind="insert_after", start=n, end=n, text=text))
            continue

        if line.strip() == "" or line.strip().startswith("#"):
            i += 1
            continue

        raise ParseError(f"Unrecognized operation line: {line!r}")

    return path, tag, ops


def _is_op_header(line: str) -> bool:
    return bool(
        _RE_PUT_RANGE.match(line)
        or _RE_PUT_BEFORE.match(line)
        or _RE_PUT_AFTER.match(line)
        or _RE_CUT_RANGE.match(line)
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
# Apply
# ---------------------------------------------------------------------------


def _apply_operations(content: str, ops: list[HashlineOp]) -> str:
    """Apply *ops* to *content*, resolving all line references before mutating.

    Line numbers reference the ORIGINAL content.  Operations are applied
    bottom-up so earlier line numbers remain valid after later insertions/deletions.

    Raises :class:`ValueError` on out-of-range line references.
    """
    had_trailing_newline = content.endswith("\n")
    file_lines = content.splitlines()
    total = len(file_lines)

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
        elif op.kind == "replace":
            new_lines = op.text.splitlines() if op.text else []
            file_lines[s:e] = new_lines
        elif op.kind == "insert_before":
            new_lines = op.text.splitlines() if op.text else []
            file_lines[s:s] = new_lines
        elif op.kind == "insert_after":
            new_lines = op.text.splitlines() if op.text else []
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

| Syntax       | Effect                                    |
|-------------|------------------------------------------|
| ``PUT N.=M:``| Replace lines N through M with new lines |
| ``PUT <N:``  | Insert new lines BEFORE line N           |
| ``PUT >N:``  | Insert new lines AFTER line N            |
| ``CUT N.=M`` | Delete lines N through M                 |

New content lines are prefixed with ``+``.  An empty line ends a content block.
CUT has no content block.  Line numbers reference the version shown by ``read``.

### Example

```
[greet.py#A1B2C3D4]
PUT 2.=3:
+    print(f"Hi, {name}")
CUT 4.=4
PUT >5:
+    return name
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
    if live_content != snapshot_content:
        yield Message(
            "system",
            f"hashline_edit: file has changed since snapshot was captured for {resolved}. "
            "Call `read` again to get a fresh snapshot.",
        )
        return

    # Apply operations
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

    # Write result
    try:
        resolved.write_text(updated, encoding="utf-8")
    except (PermissionError, OSError) as e:
        yield Message("system", f"hashline_edit: write failed: {e}")
        return

    # Update snapshot for the new content
    store_snapshot(str(resolved), updated)

    n = len(ops)
    yield Message(
        "system",
        f"hashline_edit applied to `{resolved}` ({n} operation{'s' if n != 1 else ''})",
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
