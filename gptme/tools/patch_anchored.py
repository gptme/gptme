"""
Hash-anchored file viewing and editing.

Provides ``view_anchored`` and ``patch_anchored`` tools that implement a
two-call editing cycle resistant to line-number drift:

1. ``view_anchored <path>`` — renders the file with a content-addressed
   anchor token on every line.
2. ``patch_anchored <path>`` — accepts a JSON array of edit operations
   (``{anchor, op, text, expected}``), resolves all anchors against the
   *current* file, then applies the batch atomically.  Any unresolved
   anchor or failing ``expected`` guard rejects the whole batch and
   re-renders the file so the model can retry.

Both tools are disabled by default (enable via allowlist or
``TOOL_ALLOWLIST`` env var).

Design: ``knowledge/technical-designs/2026-05-31-hash-anchored-editing-gptme-integration.md``
Engine: :mod:`gptme.tools._anchored`
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..message import Message
from ..util.context import md_codeblock
from ._anchored import EditOperation, apply_operations, snapshot_text
from .base import Parameter, ToolSpec, ToolUse

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SEPARATOR = "│"


def _render_anchored(path: Path, text: str) -> str:
    """Format file content with one anchor token per line."""
    anchors = snapshot_text(text)
    if not anchors:
        return md_codeblock(str(path), "(empty file)")
    body = "\n".join(f"{a.anchor}{_SEPARATOR} {a.text}" for a in anchors)
    return md_codeblock(str(path), body)


def _path_from_args(
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Path | None:
    if args:
        return Path(" ".join(args)).expanduser()
    if kwargs and kwargs.get("path"):
        return Path(kwargs["path"]).expanduser()
    return None


def _read_file(path: Path) -> tuple[str, str | None]:
    """Read a file; return (content, error_message).  error_message is None on success."""
    path = path.expanduser().resolve()
    if not path.exists():
        return "", f"File not found: {path}"
    if not path.is_file():
        return "", f"Not a file: {path}"
    try:
        return path.read_text(encoding="utf-8"), None
    except UnicodeDecodeError:
        return "", f"Cannot read binary file: {path}"
    except PermissionError:
        return "", f"Permission denied: {path}"


# ---------------------------------------------------------------------------
# view_anchored
# ---------------------------------------------------------------------------

_VIEW_INSTRUCTIONS = """
Render a file with content-addressed anchor tokens so that specific lines can
be referenced in a subsequent ``patch_anchored`` call.

Each output line is formatted as::

    <anchor>│ <line content>

where ``<anchor>`` is a stable context-triple hash.  Adjacent-line edits
invalidate the surrounding anchors by design, so always call ``view_anchored``
immediately before ``patch_anchored`` — never reuse stale anchors.
""".strip()


def _view_examples(tool_format) -> str:  # type: ignore[no-untyped-def]
    return f"""
> User: show me hello.py with anchors so I can edit it
> Assistant:
{ToolUse("view_anchored", ["hello.py"], "").to_output(tool_format)}
> System: ````hello.py
> a3f1c8d2e9b5a70f:1│ def hello():
> 9c2eb1d4f7a36e52:1│     print("Hello world")
> ````
""".strip()


def execute_view_anchored(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    path = _path_from_args(args, kwargs)
    if path is None:
        yield Message("system", "Error: no path provided to view_anchored")
        return

    content, err = _read_file(path)
    if err:
        yield Message("system", err)
        return

    yield Message("system", _render_anchored(path.expanduser().resolve(), content))


tool_view_anchored = ToolSpec(
    name="view_anchored",
    desc="Render a file with content-addressed anchor tokens for use with patch_anchored",
    instructions=_VIEW_INSTRUCTIONS,
    examples=_view_examples,
    execute=execute_view_anchored,
    block_types=["view_anchored"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="Path to the file to render.",
            required=True,
        ),
    ],
)

# ---------------------------------------------------------------------------
# patch_anchored
# ---------------------------------------------------------------------------

_PATCH_INSTRUCTIONS = """
Apply anchored edits to a file using anchor tokens produced by ``view_anchored``.

The code block body must be a JSON array of operation objects::

    [
      {"anchor": "<token>", "op": "replace",       "text": "<new text>"},
      {"anchor": "<token>", "op": "delete"},
      {"anchor": "<token>", "op": "insert_before", "text": "<new text>"},
      {"anchor": "<token>", "op": "insert_after",  "text": "<new text>",
       "expected": "<guard text>"}
    ]

Fields:

- ``anchor``   (string, required) — token from ``view_anchored``
- ``op``       (string, required) — one of ``replace`` / ``delete`` /
  ``insert_before`` / ``insert_after``
- ``text``     (string, optional) — new content for replace/insert ops
- ``expected`` (string, optional) — guard: if the anchored line no longer
  matches this exact text the whole batch is rejected before any mutation

All anchors are resolved against the *current* file before any write.
On any failure the batch is rejected atomically and the file is re-rendered
so the model can retry with fresh anchors.

### Two-call cycle

1. Call ``view_anchored`` to get fresh anchors.
2. Call ``patch_anchored`` with those anchors.

Never reuse anchors from a previous ``view_anchored`` call.
""".strip()


def _patch_examples(tool_format) -> str:  # type: ignore[no-untyped-def]
    ops = '[{"anchor": "9c2eb1d4f7a36e52:1", "op": "replace", "text": "    print(\\"Hello, world!\\")", "expected": "    print(\\"Hello world\\")"}]'
    return f"""
> User: update the greeting in hello.py
> Assistant: First, I'll view the file to get anchor tokens:
{ToolUse("view_anchored", ["hello.py"], "").to_output(tool_format)}
> System: ````hello.py
> a3f1c8d2e9b5a70f:1│ def hello():
> 9c2eb1d4f7a36e52:1│     print("Hello world")
> ````
> Assistant: Now I'll apply the anchored patch:
{ToolUse("patch_anchored", ["hello.py"], ops).to_output(tool_format)}
> System: Anchored patch applied to `hello.py` (1 operation(s))
""".strip()


def _parse_ops(ops_json: str) -> list[EditOperation]:
    """Parse a JSON array of operations into :class:`EditOperation` objects."""
    try:
        raw = json.loads(ops_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
    if not isinstance(raw, list):
        raise ValueError("Expected a JSON array of operations")
    ops: list[EditOperation] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"Operation {i} must be a JSON object, got {type(item).__name__}"
            )
        anchor = item.get("anchor")
        op = item.get("op")
        if not anchor:
            raise ValueError(f"Operation {i} missing required field 'anchor'")
        if not op:
            raise ValueError(f"Operation {i} missing required field 'op'")
        ops.append(
            EditOperation(
                anchor=anchor,
                op=op,
                text=item.get("text"),
                expected=item.get("expected"),
            )
        )
    return ops


def execute_patch_anchored(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    path = _path_from_args(args, kwargs)
    if path is None:
        yield Message("system", "Error: no path provided to patch_anchored")
        return

    ops_json = code or (kwargs or {}).get("ops") or ""
    if not ops_json.strip():
        yield Message("system", "Error: no operations provided to patch_anchored")
        return

    try:
        ops = _parse_ops(ops_json.strip())
    except ValueError as e:
        yield Message("system", f"patch_anchored: {e}")
        return

    content, err = _read_file(path)
    if err:
        yield Message("system", err)
        return

    resolved_path = path.expanduser().resolve()
    try:
        updated = apply_operations(content, ops)
    except ValueError as e:
        rerender = _render_anchored(resolved_path, content)
        yield Message(
            "system",
            f"patch_anchored failed (batch rejected, no changes written): {e}\n\n"
            f"Re-rendered file with current anchors:\n{rerender}",
        )
        return

    resolved_path.write_text(updated, encoding="utf-8")
    yield Message(
        "system",
        f"Anchored patch applied to `{resolved_path}` ({len(ops)} operation(s))",
    )


tool_patch_anchored = ToolSpec(
    name="patch_anchored",
    desc="Apply hash-anchored edits to a file using anchor tokens from view_anchored",
    instructions=_PATCH_INSTRUCTIONS,
    examples=_patch_examples,
    execute=execute_patch_anchored,
    block_types=["patch_anchored"],
    disabled_by_default=True,
    parameters=[
        Parameter(
            name="path",
            type="string",
            description="Path to the file to patch.",
            required=True,
        ),
        Parameter(
            name="ops",
            type="string",
            description='JSON array of edit operations. Each: {"anchor": "...", "op": "replace|delete|insert_before|insert_after", "text": "...", "expected": "..."}',
            required=True,
        ),
    ],
)

__doc__ = tool_view_anchored.get_doc(__doc__)
