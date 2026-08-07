"""In-memory snapshot store for hashline-anchored editing.

Maps resolved file paths to (tag, content) pairs so the hashline_edit tool
can verify that the live file still matches the version the model was shown.

The tag is the first 4 bytes (8 hex chars) of SHA-256 of the file content,
uppercased — e.g. ``A1B2C3D4``.
"""

from __future__ import annotations

import hashlib

# path (str, resolved) → (tag: str, content: str)
_store: dict[str, tuple[str, str]] = {}


def compute_tag(content: str) -> str:
    """Return an 8-hex-char (4-byte) SHA-256 prefix of the file content."""
    digest = hashlib.sha256(content.encode()).digest()
    return digest[:4].hex().upper()


def store_snapshot(path: str, content: str) -> str:
    """Store a snapshot for *path* and return its tag."""
    tag = compute_tag(content)
    _store[path] = (tag, content)
    return tag


def lookup_snapshot(path: str, tag: str) -> tuple[bool, str | None]:
    """Return (tag_known, content).

    ``tag_known`` is True when the path has a stored snapshot **and** its tag
    matches *tag*.  ``content`` is the stored content on a match, else None.
    """
    stored = _store.get(path)
    if stored is None:
        return False, None
    stored_tag, content = stored
    if stored_tag == tag:
        return True, content
    return False, None


def get_stored_tag(path: str) -> str | None:
    """Return the tag currently stored for *path*, or None."""
    stored = _store.get(path)
    return stored[0] if stored else None


def clear() -> None:
    """Clear all snapshots (used in tests)."""
    _store.clear()
