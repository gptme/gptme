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


def read_metadata(logdir: Path) -> ConversationMetadata:
    """Read the sidecar; a missing or unreadable file yields the defaults."""
    path = logdir / METADATA_FILENAME
    try:
        data = tomlkit.parse(path.read_text()).unwrap()
    except (OSError, ValueError, tomlkit.exceptions.TOMLKitError):
        return ConversationMetadata()
    return _coerce(data)


def validate_metadata_patch(patch: dict[str, Any]) -> str | None:
    """Return an error message if ``patch`` is not a valid metadata update."""
    allowed = {"starred", "description", "tags", "pinned_order"}
    if unknown := sorted(set(patch) - allowed):
        return f"Unknown metadata field(s): {', '.join(unknown)}"
    if "starred" in patch and not isinstance(patch["starred"], bool):
        return "starred must be a boolean"
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
    return None


def update_metadata(logdir: Path, patch: dict[str, Any]) -> ConversationMetadata:
    """Apply a validated partial update and write the sidecar atomically.

    ``null`` clears a field back to its default.
    """
    if error := validate_metadata_patch(patch):
        raise ValueError(error)
    meta = read_metadata(logdir)
    defaults = ConversationMetadata()
    for key, value in patch.items():
        setattr(meta, key, getattr(defaults, key) if value is None else value)

    doc = tomlkit.document()
    for key, value in meta.to_dict().items():
        if value is not None:  # TOML has no null
            doc[key] = value
    fd, tmp = tempfile.mkstemp(dir=logdir, prefix=".metadata-", suffix=".toml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp, logdir / METADATA_FILENAME)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return meta
