"""Context isolation utilities for subagents.

Provides secret redaction for workspace context messages passed to subagents.
Subagents always start with a fresh conversation (no parent history is shared),
but they do inherit workspace context (files from gptme.toml, context_cmd output,
user-level config) when context_mode="full". This module helps sanitize that
inherited context.
"""

import re

from ...message import Message

# Regex that matches lines where a key name suggests a secret.
# Captures: (prefix_with_key_and_separator)(value)(optional_suffix)
# Matches patterns like:
#   API_KEY=sk-abc123
#   github_token: ghp_xyz
#   OPENAI_API_KEY="sk-..."
#   private_key=-----BEGIN RSA...
#   Bearer ghp_abc123
_SECRET_LINE_RE = re.compile(
    r"""(?ix)                                # case-insensitive, verbose
    (                                        # group 1: prefix (preserved)
        (?:
            (?:                              # name=value style
                [\w\-\.]+                    # variable name
                \s*[=:]\s*                   # separator
            )
            |
            (?:bearer\s+)                    # Bearer token header
        )
        (?:.*?                               # anything before the sensitive name
            (?:
                api[-_]?key|apikey           # API keys
                |token                       # tokens
                |secret                      # secrets
                |password|passwd             # passwords
                |private[-_]key|privkey      # private keys
                |auth[-_]?(?:key|token)      # auth credentials
                |access[-_]key               # access keys
                |credential                  # generic credential
            )
            .*?                              # anything after, before separator
            (?:\s*[=:]\s*)?                  # optional repeated separator
        )?
    )
    (["']?)                                  # group 2: optional opening quote
    (\S+)                                    # group 3: the secret value
    (["']?)                                  # group 4: optional closing quote
    """,
    re.MULTILINE,
)

# Pattern for YAML/TOML colon-style assignments (key: value)
_COLON_ASSIGN_RE = re.compile(
    r"""(?ix)
    ^(\s*)                                    # group 1: optional leading whitespace
    (                                         # group 2: variable name with secret keyword
        [\w\-]*?
        (?:
            api[-_]?key|apikey
            |token
            |secret
            |password|passwd
            |private[-_]key|privkey
            |auth[-_]?(?:key|token)
            |access[-_]key
            |credential
        )
        [\w\-]*
    )
    (\s*:\s*)                                 # group 3: colon separator
    (["']?)                                   # group 4: optional opening quote
    (.+?)                                     # group 5: the value
    (["']?)                                   # group 6: optional closing quote
    (\s*)$                                    # group 7: trailing whitespace
    """,
    re.MULTILINE,
)

# Simpler pattern for export statements and env-var assignment lines
_ENV_ASSIGN_RE = re.compile(
    r"""(?ix)
    ^(export\s+)?                            # optional 'export'
    (                                        # group 2: variable name
        [\w\-]*?                             # zero or more chars before keyword
        (?:
            api[-_]?key|apikey
            |token
            |secret
            |password|passwd
            |private[-_]key|privkey
            |auth[-_]?(?:key|token)
            |access[-_]key
            |credential
        )
        [\w\-]*                              # trailing name chars
    )
    (\s*[=]\s*)                              # group 3: assignment
    (["']?)                                  # group 4: opening quote
    (.+?)                                    # group 5: the value
    (["']?)                                  # group 6: closing quote
    (\s*)$                                   # group 7: trailing whitespace
    """,
    re.MULTILINE,
)

_REDACTED = "[REDACTED]"


def redact_secrets_from_text(content: str) -> str:
    """Redact common secret patterns from text content.

    Targets lines where the variable/field name suggests a secret:
    - API keys (API_KEY, OPENAI_API_KEY, etc.)
    - Tokens (TOKEN, ACCESS_TOKEN, GITHUB_TOKEN, etc.)
    - Passwords (PASSWORD, PASSWD)
    - Private keys (PRIVATE_KEY)
    - Auth credentials (AUTH_KEY, AUTH_TOKEN)
    - Generic credentials (CREDENTIAL, ACCESS_KEY)

    The key name and separator are preserved; only the value is replaced
    with ``[REDACTED]`` so context is not destroyed.

    Examples::

        >>> redact_secrets_from_text("GITHUB_TOKEN=ghp_abc123")
        'GITHUB_TOKEN=[REDACTED]'
        >>> redact_secrets_from_text("openai_api_key: sk-proj-abc")
        'openai_api_key: [REDACTED]'
        >>> redact_secrets_from_text("export PASSWORD=hunter2")
        'export PASSWORD=[REDACTED]'
    """
    return "".join(_redact_line(line) for line in content.splitlines(keepends=True))


def _redact_line(line: str) -> str:
    """Redact a single line if it contains a secret assignment."""
    # Preserve original line ending (last line of a file may have no trailing newline)
    ending = "\n" if line.endswith("\n") else ""

    # Try the env-var assignment pattern first (export VAR=value or VAR=value)
    match = _ENV_ASSIGN_RE.search(line)
    if match:
        export_prefix = match.group(1) or ""
        name = match.group(2)
        sep = match.group(3)
        trailing = match.group(7)
        return f"{export_prefix}{name}{sep}{_REDACTED}{trailing}{ending}"

    # Try YAML/TOML colon-style (key: value, e.g. github_token: ghp_xyz)
    match = _COLON_ASSIGN_RE.search(line)
    if match:
        indent = match.group(1)
        name = match.group(2)
        sep = match.group(3)
        trailing = match.group(7)
        return f"{indent}{name}{sep}{_REDACTED}{trailing}{ending}"

    return line


def redact_secrets_from_messages(messages: list[Message]) -> list[Message]:
    """Apply secret redaction to a list of messages.

    Returns new Message objects with secret values replaced by ``[REDACTED]``.
    Roles and other message metadata are preserved.

    Args:
        messages: List of messages to sanitize.

    Returns:
        A new list of messages with secrets redacted.
    """
    return [
        msg.replace(content=redact_secrets_from_text(msg.content)) for msg in messages
    ]
