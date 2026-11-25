"""
Browser tool by calling lynx --dump
"""

import logging
import os
import re
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

# Second-level labels under which country-code registries sell names (co.uk).
_CCTLD_SECOND_LEVELS = frozenset({"ac", "co", "com", "edu", "gov", "net", "org"})

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
        host = (urlparse(url).hostname or "").lower()
        changed = False
        for header in _set_cookie_headers(head_dump):
            parsed = _parse_set_cookie(header, url, now)
            if parsed is None:
                continue
            (domain, path, name), entry = parsed
            # A host replaces (or deletes) its own cookie in both the host-only
            # and the tail-matching form; caller cookies use the latter. Another
            # host only touches the scope it named.
            own = domain.lstrip(".") == host
            for variant in {host, f".{host}"} if own else {domain}:
                if self.entries.pop((variant, path, name), None) is not None:
                    changed = True
            if entry is not None:
                self.entries[(domain, path, name)] = entry
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
    path = urlparse(url).path
    if not path.startswith("/"):
        return "/"
    return path.rsplit("/", 1)[0] or "/"


def _cookie_domain(attribute: str, host: str) -> str | None:
    """Jar domain for a ``Domain`` attribute, or None if ``host`` may not set it.

    A response may only scope a cookie to its own host or a parent of it. TLDs
    and ``co.uk``-style suffixes are refused; there is no full public suffix
    list here (``requests``, used for PDF fetches, has none either).
    """
    domain = attribute.removeprefix(".").lower()
    is_ip = ":" in host or host.rsplit(".", 1)[-1].isdigit()
    labels = domain.split(".")
    is_suffix = len(labels) < 2 or (
        len(labels) == 2 and len(labels[1]) == 2 and labels[0] in _CCTLD_SECOND_LEVELS
    )
    if domain == host:
        return host if is_ip or is_suffix else f".{domain}"
    if is_ip or is_suffix or not all(labels) or not host.endswith(f".{domain}"):
        return None
    return f".{domain}"


def _cookie_expiry(value: str) -> int | None:
    """Timestamp of an ``Expires`` attribute, or None if it is unreadable."""
    try:
        parsed = parsedate_tz(value)
        return None if parsed is None else int(mktime_tz(parsed))
    except (IndexError, OverflowError, TypeError, ValueError):
        return None


def _parse_set_cookie(
    header: str, url: str, now: int
) -> tuple[_CookieKey, _CookieEntry | None] | None:
    """Parse one Set-Cookie header.

    Returns the jar key and the row to store under it, or ``None`` as the row
    when the header deletes the cookie (expired, or an empty value, which leaves
    nothing to forward). Returns ``None`` altogether if the header is unusable.
    """
    pair, *attributes = header.split(";")
    name, separator, value = pair.partition("=")
    name = name.strip()
    value = value.strip()
    host = (urlparse(url).hostname or "").lower()
    if not separator or not name or not host:
        return None
    domain_attribute = ""
    path = _cookie_default_path(url)
    max_age: int | None = None
    expires_at: int | None = None
    secure = False
    for attribute in attributes:
        attribute_name, _, attribute_value = attribute.partition("=")
        attribute_name = attribute_name.strip().lower()
        attribute_value = attribute_value.strip()
        if attribute_name == "domain" and attribute_value:
            domain_attribute = attribute_value
        elif attribute_name == "path" and attribute_value.startswith("/"):
            path = attribute_value
        elif attribute_name == "max-age":
            if re.fullmatch(r"-?[0-9]{1,18}", attribute_value):
                max_age = int(attribute_value)
        elif attribute_name == "expires":
            parsed = _cookie_expiry(attribute_value)
            if parsed is not None:
                expires_at = parsed
        elif attribute_name == "secure":
            secure = True
    if any(char in name + value + path for char in "\r\n\t"):
        return None
    domain = _cookie_domain(domain_attribute, host) if domain_attribute else host
    if domain is None:
        return None
    # Max-Age wins over Expires, whatever order the attributes come in.
    if max_age is not None:
        expires = now + max_age
    elif expires_at is not None:
        expires = expires_at
    else:
        expires = now + _SESSION_COOKIE_TTL
    key = (domain, path, name)
    if not value or expires <= now:
        return key, None
    return key, (value, expires, secure)


def _primary_lynx_config(env: dict[str, str], deadline: float) -> str | None:
    """Path of the lynx.cfg lynx loads on its own, as reported by lynx.

    The location is compiled in (and overridable with ``LYNX_CFG``), so ask
    instead of guessing. Returns ``None`` if lynx does not report one.
    """
    page = subprocess.run(
        [
            "lynx",
            "-dump",
            "-nolist",
            "-width=1000",
            "--display_charset=utf-8",
            "LYNXCFG:",
        ],
        env={**env, "LC_ALL": "C"},  # the label below is translated
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_remaining(deadline),
        check=False,  # no report just means nothing to include
    ).stdout
    match = re.search(r"#Your primary configuration (.+)", page)
    return match.group(1).strip() if match else None


def _create_cookie_jar(
    cookies: dict[str, str], host: str, base_config: str | None
) -> _CookieJar:
    """Create a private jar seeded with the caller's cookies, plus a lynx config.

    The config file is what enables ``PERSISTENT_COOKIES``; lynx ignores
    ``-cookie_file`` without it. ``-cfg`` replaces lynx's own configuration, so
    ``base_config`` is included first to keep site settings such as proxies.
    Seed cookies are host-only for ``host``.
    """
    fd, cookie_file = tempfile.mkstemp(suffix=".txt", prefix="lynx_cookies_")
    jar = _CookieJar(cookie_file, "")
    fd_open = True
    try:
        Path(cookie_file).chmod(0o600)
        with os.fdopen(fd, "w") as handle:
            fd_open = False
            handle.write("# Netscape HTTP Cookie File\n")
        # Its own mkstemp: lynx obeys this file, so nobody else may create it.
        config_fd, jar.config_path = tempfile.mkstemp(
            suffix=".cfg", prefix="lynx_cookies_"
        )
        with os.fdopen(config_fd, "w", encoding="utf-8") as handle:
            if base_config:
                handle.write(f"INCLUDE:{base_config}\n")
            handle.write("PERSISTENT_COOKIES:TRUE\n")
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
        if jar.config_path:
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
        _create_cookie_jar(
            cookies or {}, cookie_host or "", _primary_lynx_config(env, deadline)
        )
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
