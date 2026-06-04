"""Hash-anchored line editing engine.

Provides content-addressed line anchors that survive line-number drift
from unrelated edits, resolving the failure mode where conflict-marker
patches lose their footing after sequential edits in the same file.

Anchors are computed as blake2s(prev_line \0 line \0 next_line), binding
each line to its local neighborhood so that a stale anchor (from an
adjacent-line change) fails loudly rather than silently resolving to the
wrong place. Duplicate identical lines are disambiguated by an ordinal.

This is a pure library module — it has no CLI and no gptme tool
registration. Tool surfaces (``view_anchored`` / ``patch_anchored``) are
built on top of it in separate modules.

Design note: :file:`knowledge/technical-designs/2026-05-31-hash-anchored-editing-gptme-integration.md`
(bob workspace).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

OperationKind = Literal["replace", "delete", "insert_before", "insert_after"]


@dataclass(frozen=True)
class LineAnchor:
    """A content-addressed anchor for one line of text.

    The ``anchor`` field is the canonical display-and-resolve token:
    ``<digest>:<ordinal>``, e.g. ``a3f1:1``.
    """

    anchor: str
    digest: str
    ordinal: int
    line_no: int
    text: str
    prev_text: str
    next_text: str


@dataclass(frozen=True)
class EditOperation:
    """A single anchored edit operation.

    Fields:

    - ``anchor``: an anchor token produced by :func:`snapshot_text`
    - ``op``: the kind of operation to apply
    - ``text``: text block for ``replace`` / ``insert_before`` /
      ``insert_after`` (``None`` for ``delete``)
    - ``expected``: optional guard — if set and the line at the
      resolved position no longer matches ``expected``, the entire
      batch is rejected before any mutations are applied
    """

    anchor: str
    op: OperationKind
    text: str | None = None
    expected: str | None = None


def _line_digest(prev_text: str, text: str, next_text: str) -> str:
    """Compute a context-triple digest for one line."""
    payload = f"{prev_text}\0{text}\0{next_text}".encode()
    return hashlib.blake2s(payload, digest_size=8).hexdigest()


def snapshot_text(text: str) -> list[LineAnchor]:
    """Annotate every line of ``text`` with a content-addressed anchor.

    The returned list is 1:1 with the lines of ``text`` (index *i* carries
    the anchor for line *i*).  Duplicate identical triples get ascending
    ordinals so the model can target a specific occurrence.
    """
    lines = text.splitlines()
    counts: dict[tuple[str, str, str], int] = {}
    anchors: list[LineAnchor] = []

    for index, line in enumerate(lines):
        prev_text = lines[index - 1] if index else ""
        next_text = lines[index + 1] if index + 1 < len(lines) else ""
        key = (prev_text, line, next_text)
        ordinal = counts.get(key, 0) + 1
        counts[key] = ordinal
        digest = _line_digest(*key)
        anchors.append(
            LineAnchor(
                anchor=f"{digest}:{ordinal}",
                digest=digest,
                ordinal=ordinal,
                line_no=index + 1,
                text=line,
                prev_text=prev_text,
                next_text=next_text,
            )
        )
    return anchors


def _split_block(text: str | None) -> list[str]:
    if text is None:
        return []
    return text.splitlines()


def apply_operations(text: str, operations: list[EditOperation]) -> str:
    """Atomically resolve and apply a batch of anchored edit operations.

    All anchors are resolved against the **current** file before any
    mutation happens.  If any anchor is unknown, or any ``expected``
    guard fails, the entire batch is rejected with a :class:`ValueError`
    and ``text`` is returned unchanged.
    """
    had_trailing_newline = text.endswith("\n")
    crlf = "\r\n" in text
    lines = text.splitlines()
    anchors = {item.anchor: item for item in snapshot_text(text)}

    resolved: list[tuple[int, EditOperation]] = []
    seen_anchors: set[str] = set()

    for operation in operations:
        anchor = anchors.get(operation.anchor)
        if anchor is None:
            raise ValueError(f"Unknown anchor: {operation.anchor}")
        if operation.anchor in seen_anchors:
            raise ValueError(
                f"Multiple operations target the same anchor: {operation.anchor}"
            )
        seen_anchors.add(operation.anchor)

        if (
            operation.op in ("replace", "insert_before", "insert_after")
            and operation.text is None
        ):
            raise ValueError(
                f"Operation '{operation.op}' requires text but got None for anchor {operation.anchor}"
            )

        line_index = anchor.line_no - 1
        if operation.expected is not None and lines[line_index] != operation.expected:
            raise ValueError(
                f"Anchor {operation.anchor} no longer matches expected text: "
                f"{lines[line_index]!r} != {operation.expected!r}"
            )
        resolved.append((line_index, operation))

    # Apply bottom-up so line indices above the current edit stay valid.
    for line_index, operation in sorted(
        resolved, key=lambda item: item[0], reverse=True
    ):
        block = _split_block(operation.text)
        if operation.op == "replace":
            lines[line_index : line_index + 1] = block
        elif operation.op == "delete":
            lines[line_index : line_index + 1] = []
        elif operation.op == "insert_before":
            lines[line_index:line_index] = block
        elif operation.op == "insert_after":
            lines[line_index + 1 : line_index + 1] = block
        else:  # pragma: no cover
            raise ValueError(f"Unsupported operation: {operation.op}")

    result = "\n".join(lines)
    if had_trailing_newline:
        result += "\n"
    if crlf:
        result = result.replace("\n", "\r\n")
    return result
