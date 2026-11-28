"""User-facing conversation metadata stored in a ``metadata.toml`` sidecar.

Holds the fields the webui edits on a conversation (``starred``,
``description``, ``tags``, ``pinned_order``). They are kept out of
``config.toml`` because they describe the conversation, not how the agent
runs, and changing them must not touch the chat config.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

METADATA_FILENAME = "metadata.toml"

# Failures that mean the sidecar exists but cannot be read or parsed. Editing
# must never overwrite it, and callers (the server) should surface them instead
# of crashing.
METADATA_READ_ERRORS = (OSError, ValueError, tomlkit.exceptions.TOMLKitError)


@dataclass
class ConversationMetadata:
    starred: bool = False
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    pinned_order: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce(data: dict[str, Any]) -> ConversationMetadata:
    """Build metadata from a parsed sidecar, ignoring malformed fields."""
    meta = ConversationMetadata()
    if isinstance(data.get("starred"), bool):
        meta.starred = data["starred"]
    if isinstance(data.get("description"), str):
        meta.description = data["description"]
    tags = data.get("tags")
    if isinstance(tags, list):
        meta.tags = [t for t in tags if isinstance(t, str)]
    pinned = data.get("pinned_order")
    if isinstance(pinned, int) and not isinstance(pinned, bool):
        meta.pinned_order = pinned
    return meta


def _load_document(path: Path) -> tomlkit.TOMLDocument:
    """Load the sidecar for editing.

    A missing sidecar yields an empty document; any other read/parse failure
    propagates so an edit cannot overwrite data it could not read.
    """
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return tomlkit.document()


def read_metadata(logdir: Path, *, strict: bool = False) -> ConversationMetadata:
    """Read the sidecar, tolerating unreadable files unless editing with strict=True.

    A missing sidecar always yields defaults; other read/parse failures must
    propagate on the write path so an edit cannot overwrite unreadable data.
    """
    path = logdir / METADATA_FILENAME
    try:
        return _coerce(_load_document(path).unwrap())
    except METADATA_READ_ERRORS:
        if strict:
            raise
        return ConversationMetadata()


def validate_metadata_patch(patch: dict[str, Any]) -> str | None:
    """Return an error message if ``patch`` is not a valid metadata update."""
    allowed = {"starred", "description", "tags", "pinned_order"}
    if unknown := sorted(set(patch) - allowed):
        return f"Unknown metadata field(s): {', '.join(unknown)}"
    if "starred" in patch and not isinstance(patch["starred"], bool | type(None)):
        return "starred must be a boolean or null"
    if "description" in patch and not isinstance(
        patch["description"], str | type(None)
    ):
        return "description must be a string or null"
    if "tags" in patch:
        tags = patch["tags"]
        if tags is not None and (
            not isinstance(tags, list) or not all(isinstance(t, str) for t in tags)
        ):
            return "tags must be a list of strings or null"
    if "pinned_order" in patch:
        pinned = patch["pinned_order"]
        if pinned is not None and (
            not isinstance(pinned, int) or isinstance(pinned, bool)
        ):
            return "pinned_order must be an integer or null"
    texts = [patch.get("description")] + list(patch.get("tags") or [])
    for text in texts:
        if isinstance(text, str):
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                return "description and tags must be valid Unicode text"
    return None


def update_metadata(logdir: Path, patch: dict[str, Any]) -> ConversationMetadata:
    """Apply a validated partial update and write the sidecar atomically.

    Only the fields named in ``patch`` are touched: keys this version does not
    know about (a newer gptme field, or data another tool stored) and fields
    that parse but fail coercion survive an unrelated edit. ``null`` removes a
    field so it reads back as its default.
    """
    if error := validate_metadata_patch(patch):
        raise ValueError(error)
    path = logdir / METADATA_FILENAME
    doc = _load_document(path)
    for key, value in patch.items():
        if value is None:  # TOML has no null; absence means the default
            doc.pop(key, None)
        else:
            doc[key] = value

    fd, tmp = tempfile.mkstemp(dir=logdir, prefix=".metadata-", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return _coerce(doc.unwrap())
