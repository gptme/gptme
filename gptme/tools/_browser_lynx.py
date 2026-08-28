"""
Browser tool by calling lynx --dump
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
_MAX_INPUT_LENGTH = 2048


def _validate_url_scheme(url: str) -> None:
    """Validate that a URL is safe to pass to lynx.

    Security: Prevents file:// protocol from reading local files.
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


def read_url(url: str, cookies: dict | None = None) -> str:
    # Security: validate URL scheme before passing to lynx
    _validate_url_scheme(url)

    env = os.environ.copy()
    cmd = ["lynx", "--dump", url, "--display_charset=utf-8"]

    cookie_file = None
    if cookies:
        # Create Netscape-format cookie file for lynx
        parsed = urlparse(url)
        domain = parsed.hostname
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
            timeout=60,
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
