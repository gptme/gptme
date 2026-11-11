"""Secret redaction for text that enters a conversation automatically.

Used to sanitize content that gptme injects into a session on its own — e.g.
the dirty-working-tree diff hook — as well as subagent workspace context and
shell tool output (where a value must also look like a credential).
Targets lines whose variable/field name marks them as a secret so a value is
replaced with ``[REDACTED]`` while the surrounding context is preserved.

The patterns are intentionally name-based (``*TOKEN*``, ``*PASSWORD*``, ...)
rather than entropy-based: this is a cheap, predictable guard against the most
common accidental leak (a credential edited in a tracked file), not a
general-purpose secret scanner.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    ^(\s*(?:export\s+)?)                     # optional leading whitespace and/or 'export'
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
    as secret-shaped when it carries a known vendor prefix or is a long,
    unprefixed opaque token — the shapes real leaked credentials have.
    """
    v = value.strip().strip("'\"")
    if not v:
        return False
    if _SECRET_VALUE_PREFIX_RE.match(v):
        return True
    # Unprefixed high-entropy token: long, no path-ish separators (so a
    # filename with an extension or underscores is not mistaken for a secret),
    # containing a digit. Accept base64-ish (mixed case) or a long lowercase
    # hex run — the two shapes of unprefixed vendor-less keys.
    if not re.fullmatch(r"[A-Za-z0-9+/=]{24,}", v) or not any(c.isdigit() for c in v):
        return False
    return any(c.isupper() for c in v) or bool(re.fullmatch(r"[0-9a-f]{24,}", v))


def redact_secret_values_in_path(path: str) -> str:
    """Redact only the secret-shaped value of an assignment-shaped path.

    For diff header paths and untracked filenames, where the segment is a path
    rather than a line: ``token=parser.py`` is preserved (the value is a
    filename), while ``GITHUB_TOKEN=ghp_...`` becomes ``GITHUB_TOKEN=[REDACTED]``.
    Any suffix after the secret value (an extension, a space inside a quoted
    path) is kept — the redaction must not destroy the surrounding filename.
    The suffix is itself passed through this redactor, so a filename holding
    several assignments (``GITHUB_TOKEN=ghp_x PASSWORD=hunter2``) loses every
    credential, not just the first.
    """
    ending = "\n" if path.endswith("\n") else ""
    body = path[: -len(ending)] if ending else path
    match = _PATH_ASSIGN_RE.match(body)
    # Unescape git C-quoting so an escaped-quote-wrapped value
    # (``\"ghp_...\"``) still matches the vendor prefixes; strip the
    # surrounding quote characters the unescape may leave behind.
    value = match.group(3).replace('\\"', '"').strip('"') if match else ""
    if match and looks_like_secret_value(value):
        prefix_match = _SECRET_VALUE_PREFIX_RE.match(value)
        if prefix_match:
            # Vendor-prefixed value: redact the whole token, but keep any
            # whitespace-separated suffix — a space inside a quoted path is
            # filename remainder, not part of the secret
            # (``ghp_leak dir/x`` -> ``[REDACTED] dir/x``).
            rest = value[prefix_match.end() :]
            ws = next((i for i, c in enumerate(rest) if c.isspace()), len(rest))
            redacted_value = _REDACTED
            if ws < len(rest):
                suffix = rest[ws:].lstrip()
                if suffix:
                    # A further secret-named assignment in the remainder is a
                    # second credential, not filename remainder — redact it
                    # name-based (shape gating would let ``PASSWORD=hunter2``
                    # through). Anything else (``dir/file.txt``) stays readable.
                    suffix_match = _PATH_ASSIGN_RE.match(suffix)
                    if suffix_match:
                        redacted_value += f" {suffix_match.group(1)}{suffix_match.group(2)}{_REDACTED}"
                    else:
                        redacted_value += " " + suffix
        else:
            redacted_value = _REDACTED
        return f"{match.group(1)}{match.group(2)}{redacted_value}{ending}"
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


def _looks_like_output_secret(value: str) -> bool:
    """Match credential literals, not source expressions or filename rules."""
    # Assignment regexes remove an optional pair of outer quotes. Quotes left
    # inside the value (e.g. 'ghp_" + suffix') identify an expression, not a key.
    if not re.fullmatch(r"[A-Za-z0-9_./+=-]+", value):
        return False
    if _SECRET_VALUE_PREFIX_RE.match(value):
        return True
    # JWTs have three base64url segments; unlike paths, dots are meaningful.
    if re.fullmatch(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
        return True
    if not re.fullmatch(r"[A-Za-z0-9_+/=-]{24,}", value):
        return False
    return any(c.isdigit() for c in value) and (
        any(c.isupper() for c in value) or bool(re.fullmatch(r"[0-9a-f]{24,}", value))
    )


# Credential literals that can appear anywhere in a line (inside a dict repr,
# a failing assertion, a mock-call repr or a header), not only after a
# line-leading ``NAME=``. Vendor prefixes are high precision, so these need no
# surrounding name; a minimum body length keeps prose such as ``sk-learn`` out.
_EMBEDDED_VENDOR_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-(?:(?:ant-|or-|proj-|svcacct-)[A-Za-z0-9_-]{20,}|[A-Za-z0-9_]{20,}[A-Za-z0-9_-]*)"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|AIza[0-9A-Za-z_-]{30,}"
    r"|ya29\.[A-Za-z0-9_-]{20,}"
    r"|npm_[A-Za-z0-9]{30,}"
    r"|dckr_pat_[A-Za-z0-9_-]{16,}"
    r"|[rsp]k_(?:live|test)_[A-Za-z0-9]{16,}"
    # JWT segments include '_' and '-': neither can allow another start inside
    # a segment, or repeated 'eyJ_' prefixes retry every remaining suffix.
    # Consume diff deletion markers from a boundary, preserving them on output.
    r"|(?<![_-])(?P<jwt_diff>-*)eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r")(?![A-Za-z0-9])"
)

# ``'NAME': 'value'`` / ``NAME='value'`` pairs mid-line: pytest assertion
# introspection, ``env = {...}`` locals and call reprs print them this way.
# Match each complete name once, then classify it separately. Searching for a
# keyword inside an unanchored variable-length name retries at every character;
# repeated keywords can also backtrack through every possible name suffix.
_EMBEDDED_PAIR_RE = re.compile(
    r"""(?ix)
    (?<![\w\-])
    (?P<key>["']?[\w\-]+["']?)
    (?P<sep>\s*[:=]\s*)
    (?P<q>["'])(?P<val>[A-Za-z0-9_./+=-]{20,})(?P=q)
    """
)
_EMBEDDED_SECRET_NAME_RE = re.compile(
    r"api[-_]?key|apikey|token|secret|password|passwd|private[-_]key"
    r"|privkey|auth[-_]?(?:key|token)|access[-_]key|credential",
    re.IGNORECASE,
)


def _is_credential_literal(value: str) -> bool:
    """Stricter than :func:`_looks_like_output_secret` for mid-line matches."""
    return len(value) >= 20 and _looks_like_output_secret(value)


def _redact_embedded_secrets(line: str) -> tuple[str, bool]:
    """Redact credential literals that are not at the start of a line."""
    changed = False

    def _pair(match: re.Match[str]) -> str:
        nonlocal changed
        if not _EMBEDDED_SECRET_NAME_RE.search(match.group("key")) or not (
            _is_credential_literal(match.group("val"))
        ):
            return match.group(0)
        changed = True
        q = match.group("q")
        return f"{match.group('key')}{match.group('sep')}{q}{_REDACTED}{q}"

    def _literal(match: re.Match[str]) -> str:
        nonlocal changed
        # Vendor shapes already enforce a prefix and minimum body length;
        # legitimate credentials do not necessarily contain decimal digits.
        changed = True
        return (match.group("jwt_diff") or "") + _REDACTED

    line = _EMBEDDED_PAIR_RE.sub(_pair, line)
    line = _EMBEDDED_VENDOR_LITERAL_RE.sub(_literal, line)
    return line, changed


# Values of secret-named environment variables are redacted wherever they
# appear. Shape rules cannot see an unprefixed opaque value printed without its
# name (the one verbatim leak in a seven-day trajectory census was a
# 64-character R2 secret inside a dict repr under a non-secret key); the process
# environment is the one place the harness already holds those exact values.
# The set is rebuilt per call: ``os.environ`` gains entries after import
# (dotenv loading) and scanning it costs microseconds next to the output scan.
_LIVE_SECRET_MIN_LEN = 16


def _is_live_secret_candidate(value: str) -> bool:
    """Credential-literal shaped: long and opaque, not a URL, key, path or number.

    ``:`` excludes URLs (``https://``) and namespaced identifiers
    (``cascade:task:x``), which dominate the false matches of a name-only rule.
    """
    return (
        len(value) >= _LIVE_SECRET_MIN_LEN
        and ":" not in value
        and not value.startswith(("/", "~", "."))
        and not any(c.isspace() for c in value)
        and not value.isdigit()
    )


def _live_secret_literals(environ: Mapping[str, str] | None = None) -> list[str]:
    """Secret-named environment values worth redacting, longest first.

    Longest first so a value that is a prefix of another is replaced whole
    instead of leaving the remainder of the longer one behind.
    """
    env = os.environ if environ is None else environ
    literals = {
        value
        for name, value in env.items()
        if _EMBEDDED_SECRET_NAME_RE.search(name) and _is_live_secret_candidate(value)
    }
    return sorted(literals, key=lambda v: (-len(v), v))


def _redact_live_literals(line: str, literals: list[str]) -> tuple[str, bool]:
    """Replace every occurrence of a live secret value, longest first."""
    changed = False
    for literal in literals:
        if literal in line:
            line = line.replace(literal, _REDACTED)
            changed = True
    return line, changed


def redact_secret_values_from_output(content: str) -> tuple[str, int]:
    """Redact secret-shaped values on secret-named lines of command output.

    Stricter than :func:`redact_secrets_from_text`: command output often holds
    source code (``cat config.py``), where ``TOKEN_LIMIT = 4096`` or
    ``API_KEY = os.environ["X"]`` must stay intact for later edits. A line is
    redacted only when its name marks a secret *and* its value has the shape of
    a credential literal (including JWTs and opaque URL-safe tokens), which is what an
    accidental ``env`` or ``cat .env`` dump prints. Exact values of the
    process's own secret-named environment variables are redacted in any
    shape, since no name or prefix rule can recognise an unprefixed value
    printed under an unrelated key.

    Returns the redacted text and the number of lines changed.
    """
    literals = _live_secret_literals()
    # One substring scan over the whole output decides whether the per-line
    # pass runs at all; most outputs contain no live value.
    if not any(literal in content for literal in literals):
        literals = []
    redacted = 0
    out: list[str] = []
    for line in content.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        # Exact values must run first: shape-based passes can otherwise replace
        # only a prefix, leaving a tail that no longer matches the live value.
        live_changed = False
        if literals:
            body, live_changed = _redact_live_literals(body, literals)
        match = _ENV_ASSIGN_RE.search(body) or _COLON_ASSIGN_RE.search(body)
        # A dotenv comment is not part of the credential literal. Remove the
        # comment only for classification; redaction still covers the full RHS.
        value = (
            re.split(r"\s+#", match.group(5), maxsplit=1)[0].rstrip("\"'")
            if match
            else ""
        )
        if match and _looks_like_output_secret(value):
            body, changed = _redact_line(body), True
        else:
            body, changed = _redact_embedded_secrets(body)
        changed = changed or live_changed
        out.append(body + ending)
        redacted += changed
    return "".join(out), redacted


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
