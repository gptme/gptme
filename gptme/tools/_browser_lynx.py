"""
Browser tool by calling lynx --dump
"""

import logging
import os
import subprocess
import tempfile
import threading
import time
from email.utils import mktime_tz, parsedate_tz
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse

from ._url_safety import _MAX_INPUT_LENGTH, _get_allow_hosts, _validate_url_scheme

logger = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# One budget for the whole fetch: redirect probes plus the final request.
_FETCH_TIMEOUT = 60.0
_MAX_PROBE_HEADER_BYTES = 64 * 1024


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
    """Return the absolute redirect target from a lynx HTTP header block."""
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
    """Read only a bounded header block, then stop and reap the GET probe.

    A reader thread keeps pipe reads deadline-bound on Windows too. Never use
    communicate(): its capture would buffer the discarded raw body in memory.
    """
    with subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    ) as process:
        assert process.stdout is not None
        stream = process.stdout
        headers = bytearray()

        def read_headers() -> None:
            while len(headers) <= _MAX_PROBE_HEADER_BYTES:
                line = stream.readline(_MAX_PROBE_HEADER_BYTES + 1 - len(headers))
                if not line:
                    break
                headers.extend(line)
                if line in (b"\n", b"\r\n"):
                    break

        reader = threading.Thread(target=read_headers, daemon=True)
        reader.start()
        try:
            reader.join(_remaining(deadline))
            if reader.is_alive():
                raise subprocess.TimeoutExpired(cmd, _FETCH_TIMEOUT)
            if len(headers) > _MAX_PROBE_HEADER_BYTES:
                raise ValueError("Lynx response headers exceed 64 KiB")
            return headers.decode("utf-8", errors="replace")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join()


# A session cookie (no expiry of its own) still has to be written with a real
# deadline: lynx drops jar rows whose expiry is 0 when it loads them.
_SESSION_COOKIE_TTL = 3600

# Netscape jar row: domain, path, name -> value, expiry, secure.
_CookieKey = tuple[str, str, str]
_CookieEntry = tuple[str, int, bool]


class _CookieJar:
    """Per-request cookie jar that lynx reads but never writes.

    Under an allowlist each redirect hop is a separate lynx process, and lynx
    only loads ``-cookie_file`` when ``PERSISTENT_COOKIES`` is enabled in its
    config while never storing cookies back in ``-dump`` mode. So the harness
    owns the file: every response's ``Set-Cookie`` headers are written to the
    jar before the next probe and the final fetch start.
    """

    def __init__(self, path: str, config_path: str) -> None:
        self.path = path
        self.config_path = config_path
        self.entries: dict[_CookieKey, _CookieEntry] = {}

    def absorb(self, head_dump: str, url: str) -> None:
        """Store the response's cookies so later requests send them."""
        now = int(time.time())
        changed = False
        for header in _set_cookie_headers(head_dump):
            parsed = _parse_set_cookie(header, url, now)
            if parsed is None:
                continue
            key, entry = parsed
            if self.entries.get(key) != entry:
                self.entries[key] = entry
                changed = True
        if changed:
            self.flush()

    def flush(self) -> None:
        rows = ["# Netscape HTTP Cookie File\n"]
        for (domain, path, name), (value, expires, secure) in sorted(
            self.entries.items()
        ):
            tail_match = "TRUE" if domain.startswith(".") else "FALSE"
            rows.append(
                f"{domain}\t{tail_match}\t{path}\t"
                f"{'TRUE' if secure else 'FALSE'}\t{expires}\t{name}\t{value}\n"
            )
        Path(self.path).write_text("".join(rows), encoding="utf-8")


def _set_cookie_headers(head_dump: str) -> list[str]:
    """Return every Set-Cookie value of an HTTP header block."""
    headers: list[str] = []
    for line in head_dump.splitlines()[1:]:
        if not line.strip():
            break
        name, _, value = line.partition(":")
        if name.strip().lower() == "set-cookie":
            headers.append(value.strip())
    return headers


def _cookie_default_path(url: str) -> str:
    """Directory of the request path, the RFC 6265 default cookie Path."""
    path = urlparse(url).path or "/"
    if not path.startswith("/") or path.count("/") <= 1:
        return "/"
    return path.rsplit("/", 1)[0] + "/"


def _parse_set_cookie(
    header: str, url: str, now: int
) -> tuple[_CookieKey, _CookieEntry] | None:
    """Parse one Set-Cookie header into a jar row, or None if unusable."""
    name, separator, remainder = header.partition("=")
    name = name.strip()
    value = remainder.split(";", 1)[0].strip()
    if not separator or not name or not value:
        # An empty value is a deletion, and there is nothing to forward.
        return None
    if any(char in name + value for char in "\r\n\t"):
        return None
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    domain = host
    path = _cookie_default_path(url)
    expires = now + _SESSION_COOKIE_TTL
    secure = False
    for attribute in remainder.split(";")[1:]:
        attribute_name, _, attribute_value = attribute.partition("=")
        attribute_name = attribute_name.strip().lower()
        attribute_value = attribute_value.strip()
        if attribute_name == "domain" and attribute_value:
            domain = "." + attribute_value.lstrip(".").lower()
        elif attribute_name == "path" and attribute_value.startswith("/"):
            path = attribute_value
        elif attribute_name == "max-age" and attribute_value.lstrip("-").isdigit():
            expires = now + int(attribute_value)
        elif attribute_name == "expires":
            parsed = parsedate_tz(attribute_value)
            if parsed is not None:
                expires = int(mktime_tz(parsed))
        elif attribute_name == "secure":
            secure = True
    if expires <= now:
        return None
    return (domain, path, name), (value, expires, secure)


def _create_cookie_jar(cookies: dict[str, str], host: str) -> _CookieJar:
    """Create a private jar seeded with the caller's cookies, plus a lynx config.

    The config file is what enables ``PERSISTENT_COOKIES``; lynx ignores
    ``-cookie_file`` without it. Seed cookies are host-only for ``host``.
    """
    fd, cookie_file = tempfile.mkstemp(suffix=".txt", prefix="lynx_cookies_")
    jar = _CookieJar(cookie_file, f"{cookie_file}.cfg")
    fd_open = True
    try:
        Path(cookie_file).chmod(0o600)
        with os.fdopen(fd, "w") as handle:
            fd_open = False
            handle.write("# Netscape HTTP Cookie File\n")
        Path(jar.config_path).write_text("PERSISTENT_COOKIES:TRUE\n", encoding="utf-8")
        expires = int(time.time()) + _SESSION_COOKIE_TTL
        for name, value in cookies.items():
            # Tail-matching domain, as before: these are the caller's cookies
            # for the requested host.
            jar.entries[(f".{host}", "/", name)] = (value, expires, False)
        jar.flush()
    except Exception:
        if fd_open:
            os.close(fd)
        Path(cookie_file).unlink(missing_ok=True)
        Path(jar.config_path).unlink(missing_ok=True)
        raise
    return jar


def _resolve_allowlisted_redirects(
    url: str,
    env: dict[str, str],
    deadline: float | None = None,
    cookie_args: list[str] | None = None,
    jar: _CookieJar | None = None,
) -> str:
    """Follow redirects with GET probes, allowlist-checking every hop first.

    lynx follows redirects to any host on its own, so with a host allowlist the
    real fetch runs with ``-noredir`` against the URL resolved here. Probes use
    GET because servers may handle HEAD differently; lynx prints the header
    block with ``-mime_header``. All probes share ``deadline``. ``cookie_args``
    go to every probe: a server may redirect differently without them (Google
    sends cookie-less requests to its consent page). ``jar`` carries cookies
    between hops, which otherwise only survive when one process follows the
    whole chain.
    """
    if deadline is None:
        deadline = time.monotonic() + _FETCH_TIMEOUT
    extra = cookie_args or []
    for _ in range(_MAX_REDIRECT_HOPS):
        dump = _probe_headers(
            ["lynx", "-mime_header", "-source", "-noredir", *extra, url],
            env,
            deadline,
        )
        if jar is not None:
            jar.absorb(dump, url)
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
    allow_hosts = _get_allow_hosts()

    if cookies:
        assert cookie_host is not None  # Guaranteed by _validate_url_scheme().
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

    # Under an allowlist every hop is a separate lynx process, so a jar is
    # needed even without caller cookies: a server can set a session cookie on
    # the redirect that the next hop (and the final fetch) expects back.
    jar = (
        _create_cookie_jar(cookies or {}, cookie_host or "")
        if cookies or allow_hosts is not None
        else None
    )
    cookie_args = (
        [f"-cfg={jar.config_path}", f"-cookie_file={jar.path}", "-accept_all_cookies"]
        if jar is not None
        else []
    )

    try:
        if allow_hosts is not None:
            # Only the first URL was checked above; lynx would otherwise follow a
            # redirect to any host and return its content.
            url = _resolve_allowlisted_redirects(url, env, deadline, cookie_args, jar)
            cmd = ["lynx", "--dump", "-noredir", url, "--display_charset=utf-8"]
        else:
            cmd = ["lynx", "--dump", url, "--display_charset=utf-8"]
        cmd.extend(cookie_args)
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
        if jar is not None:
            Path(jar.path).unlink(missing_ok=True)
            Path(jar.config_path).unlink(missing_ok=True)


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
