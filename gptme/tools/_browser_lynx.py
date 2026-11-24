"""
Browser tool by calling lynx --dump
"""

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from ._url_safety import _MAX_INPUT_LENGTH, _get_allow_hosts, _validate_url_scheme

logger = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Servers that reject HEAD; their redirects are read from a GET probe instead.
_HEAD_UNSUPPORTED_STATUSES = frozenset({405, 501})
# One budget for the whole fetch: redirect probes plus the final request.
_FETCH_TIMEOUT = 60.0


def _status_code(head_dump: str) -> int | None:
    lines = head_dump.splitlines()
    if not lines:
        return None
    status = lines[0].split()
    if len(status) < 2 or not status[0].upper().startswith("HTTP/"):
        return None
    return int(status[1]) if status[1].isdigit() else None


def _remaining(deadline: float) -> float:
    """Seconds left before ``deadline``; a spent budget times out immediately."""
    return max(deadline - time.monotonic(), 0.01)


def _redirect_target(head_dump: str, url: str) -> str | None:
    """Return the absolute redirect target from ``lynx -head -dump`` output."""
    if _status_code(head_dump) not in _REDIRECT_STATUSES:
        return None
    for line in head_dump.splitlines()[1:]:
        if not line.strip():
            break
        name, _, value = line.partition(":")
        if name.strip().lower() == "location" and value.strip():
            return urljoin(url, value.strip())
    return None


def _probe_headers(cmd: list[str], env: dict[str, str], deadline: float) -> str:
    return subprocess.run(
        cmd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_remaining(deadline),
    ).stdout


def _resolve_allowlisted_redirects(
    url: str, env: dict[str, str], deadline: float | None = None
) -> str:
    """Follow redirects with HEAD probes, allowlist-checking every hop first.

    lynx follows redirects to any host on its own, so with a host allowlist the
    real fetch runs with ``-noredir`` against the URL resolved here. A server
    that rejects HEAD is probed with GET, whose header block lynx prints with
    ``-mime_header``. All probes share ``deadline``.
    """
    if deadline is None:
        deadline = time.monotonic() + _FETCH_TIMEOUT
    for _ in range(_MAX_REDIRECT_HOPS):
        dump = _probe_headers(
            ["lynx", "-head", "-dump", "-noredir", url], env, deadline
        )
        if _status_code(dump) in _HEAD_UNSUPPORTED_STATUSES:
            dump = _probe_headers(
                ["lynx", "-mime_header", "-source", "-noredir", url], env, deadline
            )
        target = _redirect_target(dump, url)
        if target is None:
            return url
        _validate_url_scheme(target)
        url = target
    raise RuntimeError(f"Too many redirects fetching {url}")


def read_url(url: str, cookies: dict | None = None) -> str:
    # Security: validate URL scheme before passing to lynx
    _validate_url_scheme(url)

    env = os.environ.copy()
    deadline = time.monotonic() + _FETCH_TIMEOUT
    # Caller-supplied cookies belong to the requested host, not to wherever an
    # allowlisted redirect ends up.
    cookie_host = urlparse(url).hostname
    if _get_allow_hosts() is not None:
        # Only the first URL was checked above; lynx would otherwise follow a
        # redirect to any host and return its content.
        url = _resolve_allowlisted_redirects(url, env, deadline)
        cmd = ["lynx", "--dump", "-noredir", url, "--display_charset=utf-8"]
    else:
        cmd = ["lynx", "--dump", url, "--display_charset=utf-8"]

    cookie_file = None
    if cookies:
        # Create Netscape-format cookie file for lynx
        domain = cookie_host
        assert domain is not None  # Guaranteed by _validate_url_scheme().
        for name, value in cookies.items():
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name
                or any(char in name + value for char in "\r\n\t")
            ):
                raise ValueError(
                    "Cookie names and values must not be empty or contain tabs/newlines."
                )
        fd, cookie_file = tempfile.mkstemp(suffix=".txt", prefix="lynx_cookies_")
        fd_open = True
        try:
            Path(cookie_file).chmod(0o600)
            with os.fdopen(fd, "w") as f:
                fd_open = False
                f.write("# Netscape HTTP Cookie File\n")
                for name, value in cookies.items():
                    # Format: domain, tail-match, path, secure, expiry, name, value
                    f.write(f".{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        except Exception:
            if fd_open:
                os.close(fd)
            Path(cookie_file).unlink(missing_ok=True)
            cookie_file = None
            raise
        cmd.extend([f"-cookie_file={cookie_file}", "-accept_all_cookies"])

    try:
        p = subprocess.run(
            cmd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_remaining(deadline),
        )
        return p.stdout
    finally:
        if cookie_file:
            Path(cookie_file).unlink(missing_ok=True)


def search(query: str, engine: str = "duckduckgo") -> str:
    if engine not in {"google", "duckduckgo"}:
        raise ValueError(f"Unknown search engine: {engine}")
    if not query.strip():
        raise ValueError("Search query must be non-empty.")

    query = quote_plus(query)
    if engine == "google":
        # Use SOCS cookie (newer Google consent format) to bypass GDPR banner,
        # and gl=us to avoid region-specific consent redirects.
        url = f"https://www.google.com/search?q={query}&hl=en&gl=us"
        if len(url) > _MAX_INPUT_LENGTH:
            raise ValueError(
                f"Search query URL must be no longer than {_MAX_INPUT_LENGTH} characters."
            )
        return read_url(
            url,
            cookies={
                "SOCS": "CAISHAgBEhJnd3NfMjAyMzA4MTAtMF9SQzIaAmVuIAEaBgiA_LyaBg",
                "CONSENT": "PENDING+987",
            },
        )
    if engine == "duckduckgo":
        url = f"https://lite.duckduckgo.com/lite/?q={query}"
        if len(url) > _MAX_INPUT_LENGTH:
            raise ValueError(
                f"Search query URL must be no longer than {_MAX_INPUT_LENGTH} characters."
            )
        return read_url(url)
    raise ValueError(f"Unknown search engine: {engine}")
