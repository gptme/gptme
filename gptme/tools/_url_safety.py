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


def set_session_allow_hosts(allow_hosts: list[str] | None) -> None:
    """Set the hostname allowlist for the current session context."""
    _allow_hosts_var.set(allow_hosts)


def _host_matches(hostname: str, pattern: str) -> bool:
    """Match a hostname against a pattern, supporting *.example.com wildcards.

    *.example.com matches sub.example.com but NOT example.com itself.
    """
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.com"
        return hostname.endswith(suffix)
    return hostname == pattern


def _validate_url_host(hostname: str) -> None:
    """Raise ValueError if hostname is not in the session's allow_hosts list.

    No-op when allow_hosts is None (unrestricted, the default).
    """
    allow_hosts = _allow_hosts_var.get()
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
