"""Shared URL safety checks for browser backends.

Used by the lynx backend and the PDF/requests paths in the Playwright browser
tool so file:// and credentialed URLs never reach a subprocess or HTTP client.

See: https://github.com/gptme/gptme/issues/1021
     https://github.com/gptme/gptme/pull/3663
"""

from contextvars import ContextVar
from urllib.parse import urlparse

_MAX_INPUT_LENGTH = 2048

# Session-level host allowlist. None means unrestricted (default).
_allow_hosts_var: ContextVar[list[str] | None] = ContextVar("allow_hosts", default=None)

# Process-level mirror of the allowlist. The browser runs on a dedicated thread
# (BrowserThread) that does not inherit the caller's ContextVar context, so
# checks executed there would otherwise see the default ``None`` and silently
# no-op -- disabling the restriction for the very paths (post-redirect and
# post-interaction) it exists to guard. gptme runs one chat session per CLI
# process, so a process-level value is the correct scope here.
_allow_hosts_process: list[str] | None = None


def set_session_allow_hosts(allow_hosts: list[str] | None) -> None:
    """Set the hostname allowlist for the current session."""
    global _allow_hosts_process
    _allow_hosts_process = allow_hosts
    _allow_hosts_var.set(allow_hosts)


def _get_allow_hosts() -> list[str] | None:
    """Return the effective allowlist, reading through the context to the
    process-level mirror when the current context has none set."""
    value = _allow_hosts_var.get()
    return value if value is not None else _allow_hosts_process


def parse_allow_hosts(value: str | None) -> list[str] | None:
    """Parse a comma-separated ``--allow-hosts`` / ``GPTME_ALLOW_HOSTS`` value.

    ``None`` means unrestricted. An empty (or whitespace-only) string is a
    deliberate empty allowlist that blocks every host -- it must not be
    silently coerced to ``None``, which would disable the restriction.
    Hostnames are lowercased because DNS names are case-insensitive.
    """
    if value is None:
        return None
    return [h.strip().lower() for h in value.split(",") if h.strip()]


def _host_matches(hostname: str, pattern: str) -> bool:
    """Match a hostname against a pattern, supporting *.example.com wildcards.

    *.example.com matches sub.example.com but NOT example.com itself.
    Matching is case-insensitive: DNS hostnames are not case-sensitive.
    """
    hostname = hostname.lower()
    pattern = pattern.lower()
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return hostname.endswith(suffix)
    return hostname == pattern


def _validate_url_host(hostname: str) -> None:
    """Raise ValueError if hostname is not in the session's allow_hosts list.

    No-op when allow_hosts is None (unrestricted, the default).
    """
    allow_hosts = _get_allow_hosts()
    if allow_hosts is None:
        return
    if not any(_host_matches(hostname, allowed) for allowed in allow_hosts):
        allowed_str = ", ".join(allow_hosts)
        raise ValueError(
            f"Host '{hostname}' is not in the session's allowed-hosts list "
            f"({allowed_str}). "
            f"Add it with --allow-hosts or GPTME_ALLOW_HOSTS to permit this access."
        )


def _validate_url_scheme(url: str) -> None:
    """Validate that a URL is safe to fetch over HTTP(S).

    Security: Prevents file:// protocol from reading local files, and checks
    the hostname against the session's allow_hosts list when set.
    See: https://github.com/gptme/gptme/issues/1021
    """
    if not url or len(url) > _MAX_INPUT_LENGTH:
        raise ValueError(
            f"URL must be non-empty and no longer than {_MAX_INPUT_LENGTH} characters."
        )

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc

    allowed_schemes = {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(
            f"URL scheme '{parsed.scheme}' not allowed. "
            f"Only {allowed_schemes} are permitted for security reasons."
        )
    if not hostname:
        raise ValueError("URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not include embedded credentials.")
    _validate_url_host(hostname)


def _validate_entry_url(url: str) -> None:
    """Validate a browser-tool entry URL (agent input).

    ``data:`` URLs are offline inline content (no network access; used e.g. by
    computer-use HTML fixtures) and are permitted; everything else goes through
    the strict scheme/hostname/credential validation.
    """
    if urlparse(url).scheme.lower() == "data":
        return
    _validate_url_scheme(url)


def _validate_page_url(url: str) -> None:
    """Validate the live ``page.url`` after navigation.

    The page URL is browser state, not agent input: no length limit, and the
    local schemes (``data:``, ``about:``) are offline and always permitted.
    Network URLs must still be http(s), credential-free, and inside the
    session's host allowlist.
    """
    if not url:
        return
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("Invalid URL") from exc
    scheme = parsed.scheme.lower()
    if scheme in ("data", "about"):
        return
    if scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme '{parsed.scheme}' not allowed. "
            "Only {'http', 'https'} are permitted for security reasons."
        )
    if not hostname:
        raise ValueError("URL must include a hostname.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not include embedded credentials.")
    _validate_url_host(hostname)
