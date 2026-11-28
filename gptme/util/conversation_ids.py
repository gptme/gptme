"""Helpers for validating conversation identifiers stored under the logs dir."""

import unicodedata

_MAX_CONVERSATION_ID_BYTES = 255  # Linux NAME_MAX for a single path component


def _contains_control_character(value: str) -> bool:
    """Return True when ``value`` has a control character or invisible formatting.

    ``str.isprintable()`` is False for every non-ASCII space separator, so a
    non-breaking space (U+00A0) in a conversation name was rejected as a control
    character.  Space separators are legal filename characters and the TUI can
    create and list a conversation whose name contains one; only actual control
    and formatting characters make an id unsafe.
    """
    return any(
        not ch.isprintable() and unicodedata.category(ch) != "Zs" for ch in value
    )


def conversation_id_error(value: str) -> str | None:
    """Return a validation error for unsafe conversation identifiers, if any."""
    if not value or not value.strip():
        return "conversation name cannot be empty."
    if value != value.strip():
        return "conversation name cannot start or end with whitespace."
    try:
        encoded = value.encode()
    except UnicodeEncodeError:
        return "conversation name must be valid UTF-8."
    if len(encoded) > _MAX_CONVERSATION_ID_BYTES:
        return "conversation name too long."
    if _contains_control_character(value):
        return "conversation name cannot contain control characters."
    if value == "." or "/" in value or "\\" in value or ".." in value:
        return (
            "conversation name must be a single path component "
            "(no '.', '/', '\\\\', or '..')."
        )
    return None


def conversation_id_lookup_error(value: str) -> str | None:
    """Return a validation error for looking up an existing conversation dir.

    Looser than :func:`conversation_id_error`: the rules for *naming* a new
    chat are not rules for *finding* one. ``draft..notes`` is a legal single
    directory name and ``LogManager``/the TUI create directories named that
    way, but it contains ``..``, so the naming rule would hide an existing
    conversation from ``chats read``. Callers still verify the joined path is
    a direct child of the logs dir.
    """
    if not value:
        return "conversation id cannot be empty."
    if _contains_control_character(value):
        return "conversation id cannot contain control characters."
    if value in (".", "..") or "/" in value or "\\" in value:
        return "conversation id must be a single path component."
    return None


def is_valid_conversation_lookup(value: str) -> bool:
    """Return True when ``value`` can name a direct child of the logs dir."""
    return conversation_id_lookup_error(value) is None


def is_valid_conversation_id(value: str) -> bool:
    """Return True when ``value`` is safe to use as a logs-dir child name."""
    return conversation_id_error(value) is None


def validate_conversation_id(value: str) -> str:
    """Validate and return a safe conversation identifier."""
    if error := conversation_id_error(value):
        raise ValueError(error)
    return value
