"""Context isolation utilities for subagents.

Provides secret redaction for workspace context messages passed to subagents.
Subagents always start with a fresh conversation (no parent history is shared),
but they do inherit workspace context (files from gptme.toml, context_cmd output,
user-level config) when context_mode="full". This module helps sanitize that
inherited context.

The redaction itself lives in :mod:`gptme.util.redact` so other automatic
injection paths (e.g. the dirty-diff session-start hook) can share one
implementation; the names here are re-exported for existing callers and tests.
"""

from ...util.redact import (
    redact_secrets_from_messages,
    redact_secrets_from_text,
)

__all__ = [
    "redact_secrets_from_messages",
    "redact_secrets_from_text",
]
