"""Helpers for validating conversation identifiers stored under the logs dir."""

_MAX_CONVERSATION_ID_BYTES = 255  # Linux NAME_MAX for a single path component


def conversation_id_error(value: str) -> str | None:
    """Return a validation error for unsafe conversation identifiers, if any."""
    if not value:
        return "conversation name cannot be empty."
    if len(value.encode()) > _MAX_CONVERSATION_ID_BYTES:
        return "conversation name too long."
    if (
        value == "."
        or "/" in value
        or "\\" in value
        or ".." in value
        or "\x00" in value
    ):
        return (
            "conversation name must be a single path component "
            "(no '.', '/', '\\\\', '..', or null bytes)."
        )
    return None


def is_valid_conversation_id(value: str) -> bool:
    """Return True when ``value`` is safe to use as a logs-dir child name."""
    return conversation_id_error(value) is None


def validate_conversation_id(value: str) -> str:
    """Validate and return a safe conversation identifier."""
    if error := conversation_id_error(value):
        raise ValueError(error)
    return value
