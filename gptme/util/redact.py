"""Secret redaction for text that enters a conversation automatically.

Used to sanitize content that gptme injects into a session on its own — e.g.
the dirty-working-tree diff hook — as well as subagent workspace context.
Targets lines whose variable/field name marks them as a secret so a value is
replaced with ``[REDACTED]`` while the surrounding context is preserved.

The patterns are intentionally name-based (``*TOKEN*``, ``*PASSWORD*``, ...)
rather than entropy-based: this is a cheap, predictable guard against the most
common accidental leak (a credential edited in a tracked file), not a
general-purpose secret scanner.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..message import Message

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
    ([ \t]*)$                                    # group 7: trailing whitespace (horizontal only)
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
    ([ \t]*)$                                   # group 7: trailing whitespace (horizontal only)
    """,
    re.MULTILINE,
)

_REDACTED = "[REDACTED]"

# Path context (filenames, diff headers) differs from line context: a segment
# such as ``token=parser.py`` is a legitimate filename, not a secret
# assignment, so redacting on the *name* alone destroys useful context. In a
# path we redact only when the trailing value is itself secret-shaped — the
# credible leak is a value copied into a filename, not a keyword in one.
_SECRET_VALUE_PREFIX_RE = re.compile(
    r"(?i)^("
    r"gh[pousr]_|github_pat_|"
    r"sk-(?:ant-|or-|proj-)?|xox[baprs]-|"
    r"AKIA|ASIA|glpat-|AIza|ya29\.|npm_|dckr_pat_|"
    r"rk_live_|sk_live_|pk_live_|rk_test_|sk_test_|pk_test_"
    r")"
)

# Assignment-shaped path segment: ``<secret-named-key><sep><value>``.
_PATH_ASSIGN_RE = re.compile(
    r"""(?ix)
    ^(
        [\w\-]*?
        (?:
            api[-_]?key|apikey|token|secret|password|passwd
            |private[-_]key|privkey|auth[-_]?(?:key|token)
            |access[-_]key|credential
        )
        [\w\-]*
    )
    (\s*[=:]\s*)
    (.+)$
    """,
)


def looks_like_secret_value(value: str) -> bool:
    """Heuristic: is ``value`` a credential rather than a filename token?

    Name-based matching is right for line content but wrong for paths, where a
    filename like ``token=parser.py`` is not an assignment. A value is treated
    as secret-shaped only when it carries a known vendor prefix or is a long,
    unprefixed base64-ish token — the shapes real leaked credentials have.
    """
    v = value.strip().strip("'\"")
    if not v:
        return False
    if _SECRET_VALUE_PREFIX_RE.match(v):
        return True
    # Unprefixed high-entropy token: long, mixed case + digits, and no
    # path-ish separators — a filename with an extension or underscores
    # (``my_2024_backup.tar``) is not mistaken for a secret.
    return bool(
        re.fullmatch(r"[A-Za-z0-9+/=]{24,}", v)
        and any(c.islower() for c in v)
        and any(c.isupper() for c in v)
        and any(c.isdigit() for c in v)
    )


def redact_secret_values_in_path(path: str) -> str:
    """Redact only the secret-shaped value of an assignment-shaped path.

    For diff header paths and untracked filenames, where the segment is a path
    rather than a line: ``token=parser.py`` is preserved (the value is a
    filename), while ``GITHUB_TOKEN=ghp_...`` becomes ``GITHUB_TOKEN=[REDACTED]``.
    """
    ending = "\n" if path.endswith("\n") else ""
    body = path[: -len(ending)] if ending else path
    match = _PATH_ASSIGN_RE.match(body)
    if match and looks_like_secret_value(match.group(3)):
        return f"{match.group(1)}{match.group(2)}{_REDACTED}{ending}"
    return path


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
