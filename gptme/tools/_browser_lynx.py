"""
Browser tool by calling lynx --dump
"""

import logging
import os
import subprocess
import tempfile
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _validate_url_scheme(url: str) -> None:
    """Validate that URL uses a safe scheme (http/https only).

    Security: Prevents file:// protocol from reading local files.
    See: https://github.com/gptme/gptme/issues/1021
    """
    parsed = urlparse(url)
    allowed_schemes = {"http", "https"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(
            f"URL scheme '{parsed.scheme}' not allowed. "
            f"Only {allowed_schemes} are permitted for security reasons."
        )


def read_url(url: str, cookies: dict | None = None) -> str:
    # Security: validate URL scheme before passing to lynx
    _validate_url_scheme(url)

    env = os.environ.copy()
    cmd = ["lynx", "--dump", url, "--display_charset=utf-8"]

    cookie_file = None
    if cookies:
        # Create Netscape-format cookie file for lynx
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        fd, cookie_file = tempfile.mkstemp(suffix=".txt", prefix="lynx_cookies_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write("# Netscape HTTP Cookie File\n")
                for name, value in cookies.items():
                    # Format: domain, tail-match, path, secure, expiry, name, value
                    f.write(f".{domain}\tTRUE\t/\tFALSE\t0\t{name}\t{value}\n")
        except Exception:
            os.unlink(cookie_file)
            cookie_file = None
            raise
        cmd.extend([f"-cookie_file={cookie_file}", "-accept_all_cookies"])

    try:
        p = subprocess.run(
            cmd,
            env=env,
            check=True,
            capture_output=True,
        )
        # should be utf-8, but we can't be sure
        return p.stdout.decode("utf-8", errors="replace")
    finally:
        if cookie_file and os.path.exists(cookie_file):
            os.unlink(cookie_file)


def search(query: str, engine: str = "duckduckgo") -> str:
    if engine == "google":
        # TODO: we need to figure out a way to remove the consent banner to access google search results
        #       otherwise google is not usable
        return read_url(
            f"https://www.google.com/search?q={query}&hl=en",
            cookies={"CONSENT+": "YES+42"},
        )
    if engine == "duckduckgo":
        return read_url(f"https://lite.duckduckgo.com/lite/?q={query}")
    raise ValueError(f"Unknown search engine: {engine}")
