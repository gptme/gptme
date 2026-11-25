"""Tests for lynx browser backend."""

import os
import shutil
import stat
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from gptme.tools._browser_lynx import _validate_url_scheme, read_url, search

lynx_available = shutil.which("lynx") is not None


def test_header_probe_bounds_bytes_before_decoding(monkeypatch):
    """A large raw body must not be captured or decoded in full."""
    import subprocess
    import time

    from gptme.tools import _browser_lynx

    real_popen = subprocess.Popen
    children = []

    def fake_lynx(cmd, **kwargs):
        child = real_popen(
            [
                sys.executable,
                "-c",
                "import os; "
                "os.write(1, b'HTTP/1.1 302 Found\\r\\nLocation: /next\\r\\n\\r\\n'); "
                "exec('for _ in range(32): os.write(1, b\"x\" * 65536)')",
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(_browser_lynx.subprocess, "Popen", fake_lynx)
    headers = _browser_lynx._probe_headers(
        ["lynx", "unused"], os.environ.copy(), time.monotonic() + 2
    )
    assert _browser_lynx._redirect_target(headers, "https://example.com/") == (
        "https://example.com/next"
    )
    assert len(headers) < 65536
    assert "xxxxxxxx" not in headers
    assert children[0].poll() is not None


@pytest.mark.parametrize("mode", ["oversized", "stalled"])
def test_header_probe_rejects_oversized_or_stalled_headers(monkeypatch, mode):
    import subprocess
    import time

    from gptme.tools import _browser_lynx

    real_popen = subprocess.Popen
    children = []
    code = (
        "import os; os.write(1, b'x' * 131072)"
        if mode == "oversized"
        else "import time; time.sleep(30)"
    )

    def fake_lynx(cmd, **kwargs):
        child = real_popen([sys.executable, "-c", code], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(_browser_lynx.subprocess, "Popen", fake_lynx)
    error = ValueError if mode == "oversized" else subprocess.TimeoutExpired
    with pytest.raises(error):
        _browser_lynx._probe_headers(
            ["lynx", "unused"], os.environ.copy(), time.monotonic() + 0.5
        )
    assert children[0].poll() is not None


@pytest.fixture
def mock_probe_via_run(monkeypatch):
    """Keep redirect-policy mocks separate from the real pipe-reader tests."""
    from gptme.tools import _browser_lynx

    def probe(cmd, env, deadline):
        return _browser_lynx.subprocess.run(
            cmd, env=env, timeout=_browser_lynx._remaining(deadline)
        ).stdout

    monkeypatch.setattr(_browser_lynx, "_probe_headers", probe)


def test_url_scheme_validation():
    """Test that dangerous URL schemes are blocked in lynx backend."""
    # Valid schemes should work
    _validate_url_scheme("https://example.com")
    _validate_url_scheme("http://example.com")
    _validate_url_scheme("HTTP://EXAMPLE.COM")  # Case insensitive

    # Dangerous schemes should be blocked
    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("file:///etc/passwd")

    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("ftp://example.com")

    with pytest.raises(ValueError, match="not allowed"):
        _validate_url_scheme("javascript:alert(1)")


def test_url_validation_rejects_missing_host_and_credentials():
    with pytest.raises(ValueError, match="hostname"):
        _validate_url_scheme("https://")

    with pytest.raises(ValueError, match="hostname"):
        _validate_url_scheme("https:///etc/passwd")

    with pytest.raises(ValueError, match="credentials"):
        _validate_url_scheme("https://user:password@example.com")


def test_url_validation_enforces_input_length():
    prefix = "https://example.com/"
    _validate_url_scheme(prefix + "a" * (2048 - len(prefix)))

    with pytest.raises(ValueError, match="2048"):
        _validate_url_scheme(prefix + "a" * (2049 - len(prefix)))


@pytest.mark.parametrize("cookies", [{"bad\nname": "value"}, {"name": "bad\tvalue"}])
def test_read_url_rejects_cookie_line_injection(cookies):
    with pytest.raises(ValueError, match="tabs/newlines"):
        read_url("https://example.com", cookies=cookies)


def test_read_url_cookie_file_is_private():
    observed_mode = None
    cookie_path = None

    def mock_run(cmd, **kwargs):
        nonlocal cookie_path, observed_mode
        cookie_path = next(
            arg.split("=", 1)[1] for arg in cmd if arg.startswith("-cookie_file=")
        )
        observed_mode = stat.S_IMODE(os.stat(cookie_path).st_mode)

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock page content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        read_url("https://example.com", cookies={"CONSENT": "YES"})

    assert cookie_path is not None
    assert not os.path.exists(cookie_path)
    if sys.platform != "win32":
        assert observed_mode == 0o600


def test_read_url_closes_cookie_descriptor_when_chmod_fails():
    fd, cookie_path = tempfile.mkstemp()
    try:
        with (
            patch(
                "gptme.tools._browser_lynx.tempfile.mkstemp",
                return_value=(fd, cookie_path),
            ),
            patch("gptme.tools._browser_lynx.Path.chmod", side_effect=PermissionError),
            pytest.raises(PermissionError),
        ):
            read_url("https://example.com", cookies={"CONSENT": "YES"})

        with pytest.raises(OSError, match="WinError 6|Bad file descriptor"):
            os.fstat(fd)
        assert not os.path.exists(cookie_path)
    finally:
        if os.path.exists(cookie_path):
            os.close(fd)
            os.unlink(cookie_path)


def test_search_rejects_invalid_input():
    with pytest.raises(ValueError, match="non-empty"):
        search("   ")

    with pytest.raises(ValueError, match="2048"):
        search("a" * 2049)

    with pytest.raises(ValueError, match="Unknown search engine"):
        search("query", "bing")


@pytest.mark.parametrize(
    ("engine", "url_template"),
    [
        ("google", "https://www.google.com/search?q={query}&hl=en&gl=us"),
        ("duckduckgo", "https://lite.duckduckgo.com/lite/?q={query}"),
    ],
)
def test_search_accepts_query_that_fits_final_url_limit(
    monkeypatch, engine, url_template
):
    captured_url = None

    def mock_read_url(url, cookies=None):
        nonlocal captured_url
        captured_url = url
        return "search results"

    monkeypatch.setattr("gptme.tools._browser_lynx.read_url", mock_read_url)
    query = "a" * (2048 - len(url_template.format(query="")))

    assert search(query, engine) == "search results"
    assert captured_url is not None
    assert len(captured_url) <= 2048


@pytest.mark.parametrize("engine", ["google", "duckduckgo"])
@pytest.mark.parametrize(
    "query",
    [
        "C++",
        "a&b",
        "hash#fragment",
        "a%20b",
        "a=b",
        "two words\tand\nlines",
        "naïve café",
        "ordinary phrase",
    ],
)
def test_search_preserves_literal_query(monkeypatch, engine, query):
    captured_url = None
    captured_cookies = None

    def mock_read_url(url, cookies=None):
        nonlocal captured_url, captured_cookies
        captured_url, captured_cookies = url, cookies
        return "search results"

    monkeypatch.setattr("gptme.tools._browser_lynx.read_url", mock_read_url)
    assert search(query, engine) == "search results"
    assert captured_url is not None
    parts = urlsplit(captured_url)
    params = parse_qs(parts.query)
    assert parts.fragment == ""
    assert params["q"] == [query]
    if engine == "google":
        assert parts.hostname == "www.google.com"
        assert params == {"q": [query], "hl": ["en"], "gl": ["us"]}
        assert captured_cookies is not None
        assert set(captured_cookies) == {"SOCS", "CONSENT"}
    else:
        assert parts.hostname == "lite.duckduckgo.com"
        assert params == {"q": [query]}
        assert captured_cookies is None


@pytest.mark.parametrize("engine", ["google", "duckduckgo"])
def test_search_rejects_query_that_exceeds_limit_after_encoding(monkeypatch, engine):
    def unexpected_read(*args, **kwargs):
        pytest.fail("Oversized encoded URL must be rejected before transport")

    monkeypatch.setattr("gptme.tools._browser_lynx.read_url", unexpected_read)
    # Fits as raw text but expands to 3000 ASCII characters when encoded.
    with pytest.raises(ValueError, match="2048"):
        search("+" * 1000, engine)


@pytest.mark.slow
@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_read_url():
    """Test reading URLs with lynx backend."""
    content = read_url("https://gptme.org/")
    assert "gptme" in content
    content = read_url("https://github.com/gptme/gptme/issues/205")
    assert "lynx-backed browser tool" in content


@pytest.mark.slow
@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_search():
    """Test search with lynx backend."""
    result = search("Erik Bjäreholt", "duckduckgo")
    assert "erik.bjareholt.com" in result


def test_read_url_cookie_file():
    """Test that cookies are passed to lynx via a temporary cookie file."""
    cookies = {"CONSENT": "YES+42"}
    captured_cmd = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd

        # Verify cookie file was created and contains correct content
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        assert len(cookie_args) == 1, "Expected -cookie_file argument"
        cookie_path = cookie_args[0].split("=", 1)[1]

        with open(cookie_path) as f:
            content = f.read()
        assert "# Netscape HTTP Cookie File" in content
        assert ".example.com" in content
        assert "CONSENT" in content
        assert "YES+42" in content

        assert "-accept_all_cookies" in cmd

        # Return a mock result
        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock page content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = read_url("https://example.com/search", cookies=cookies)
        assert result == "mock page content"
        assert captured_cmd is not None


def test_search_google_consent_cookies():
    """Test that Google search passes correct consent cookies."""
    captured_cmd = None
    captured_cookies = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd, captured_cookies
        captured_cmd = cmd

        # Extract and read cookie file
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        if cookie_args:
            cookie_path = cookie_args[0].split("=", 1)[1]
            with open(cookie_path) as f:
                captured_cookies = f.read()

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "search results"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = search("test query", "google")
        assert result == "search results"
        assert captured_cmd is not None
        # Verify URL includes gl=us to avoid consent redirects
        assert "gl=us" in captured_cmd[2]
        assert "hl=en" in captured_cmd[2]
        # Verify SOCS cookie (newer Google consent format)
        assert captured_cookies is not None
        assert "SOCS" in captured_cookies
        # Verify CONSENT cookie (fallback for older consent)
        assert "CONSENT" in captured_cookies
        assert "PENDING+987" in captured_cookies


def test_read_url_no_cookies():
    """Test that no cookie file is created when cookies is None."""
    captured_cmd = None

    def mock_run(cmd, **kwargs):
        nonlocal captured_cmd
        captured_cmd = cmd
        cookie_args = [arg for arg in cmd if arg.startswith("-cookie_file=")]
        assert len(cookie_args) == 0, "Should not have -cookie_file without cookies"

        from unittest.mock import MagicMock

        result = MagicMock()
        result.stdout = "mock content"
        return result

    with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
        result = read_url("https://example.com/page")
        assert result == "mock content"


@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_read_url_does_not_follow_redirect_to_disallowed_host():
    """With allow-hosts set, a redirect to another host is not followed (gptme#5126)."""
    import http.server
    import threading

    from gptme.tools._url_safety import set_session_allow_hosts

    ports: list[int] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            port = ports[0]
            if self.path.startswith("/to-other"):
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{port}/secret")
                self.end_headers()
                self.wfile.write(b"moved")
            elif self.path.startswith("/to-same"):
                self.send_response(302)
                self.send_header("Location", "/public")
                self.end_headers()
            else:
                body = b"<p>SECRET</p>" if "secret" in self.path else b"<p>PUBLIC</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    ports.append(port)
    try:
        set_session_allow_hosts(["127.0.0.1"])
        with pytest.raises(ValueError, match="localhost"):
            read_url(f"http://127.0.0.1:{port}/to-other")
        # A redirect that stays on an allowed host is still followed.
        assert "PUBLIC" in read_url(f"http://127.0.0.1:{port}/to-same")
    finally:
        set_session_allow_hosts(None)
        server.shutdown()
        server.server_close()


def test_redirect_target_parsing():
    """_redirect_target only returns a Location from a redirect status's header block."""
    from gptme.tools._browser_lynx import _redirect_target

    base = "https://example.com/a/b"
    assert _redirect_target("", base) is None
    assert _redirect_target("HTTP/1.1 200 OK\nLocation: /x\n", base) is None
    assert _redirect_target("garbage line\n", base) is None
    # Relative Location is resolved against the current URL
    assert (
        _redirect_target("HTTP/1.1 302 Found\nLocation: c\n", base)
        == "https://example.com/a/c"
    )
    # A Location after the blank line (body) is not a header
    assert (
        _redirect_target("HTTP/1.1 301 Moved\nServer: x\n\nLocation: /body\n", base)
        is None
    )
    # Redirect status without a Location header
    assert _redirect_target("HTTP/1.1 307 Temporary\nServer: x\n", base) is None


def test_resolve_allowlisted_redirects_too_many_hops(mock_probe_via_run):
    """An endless redirect chain raises instead of looping forever."""
    from subprocess import CompletedProcess

    from gptme.tools._browser_lynx import _resolve_allowlisted_redirects

    def mock_run(cmd, **kwargs):
        return CompletedProcess(
            cmd, 0, stdout="HTTP/1.1 302 Found\nLocation: /loop\n", stderr=""
        )

    with (
        patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run),
        pytest.raises(RuntimeError, match="Too many redirects"),
    ):
        _resolve_allowlisted_redirects("https://example.com/start", {})


def test_read_url_cookies_stay_scoped_to_requested_host(mock_probe_via_run):
    """Cookies go to the requested host, not to an allowlisted redirect target."""
    from subprocess import CompletedProcess

    from gptme.tools._url_safety import set_session_allow_hosts

    cookie_contents: list[str] = []

    def mock_run(cmd, **kwargs):
        if "-mime_header" in cmd:
            if "start.example.com" in cmd[-1]:
                return CompletedProcess(
                    cmd,
                    0,
                    stdout="HTTP/1.1 302 Found\nLocation: https://other.example.org/x\n",
                    stderr="",
                )
            return CompletedProcess(cmd, 0, stdout="HTTP/1.1 200 OK\n", stderr="")
        cookie_arg = next(a for a in cmd if a.startswith("-cookie_file="))
        with open(cookie_arg.split("=", 1)[1]) as f:
            cookie_contents.append(f.read())
        return CompletedProcess(cmd, 0, stdout="page", stderr="")

    try:
        set_session_allow_hosts(["start.example.com", "other.example.org"])
        with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
            assert (
                read_url("https://start.example.com/", cookies={"SID": "abc"}) == "page"
            )
    finally:
        set_session_allow_hosts(None)

    assert len(cookie_contents) == 1
    assert ".start.example.com\t" in cookie_contents[0]
    assert "other.example.org" not in cookie_contents[0]


def test_read_url_redirect_chain_shares_one_deadline(mock_probe_via_run):
    """Redirect probes and the final fetch draw on one timeout budget."""
    from subprocess import CompletedProcess

    from gptme.tools import _browser_lynx
    from gptme.tools._url_safety import set_session_allow_hosts

    clock = [1000.0]
    timeouts: list[float] = []

    def fake_monotonic():
        return clock[0]

    def mock_run(cmd, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock[0] += 25  # each request is slow but individually valid
        if "-mime_header" in cmd and not cmd[-1].endswith("/final"):
            return CompletedProcess(
                cmd, 0, stdout="HTTP/1.1 302 Found\nLocation: /final\n", stderr=""
            )
        if "-mime_header" in cmd:
            return CompletedProcess(cmd, 0, stdout="HTTP/1.1 200 OK\n", stderr="")
        return CompletedProcess(cmd, 0, stdout="page", stderr="")

    try:
        set_session_allow_hosts(["example.com"])
        with (
            patch.object(_browser_lynx.time, "monotonic", fake_monotonic),
            patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run),
        ):
            read_url("https://example.com/start")
    finally:
        set_session_allow_hosts(None)

    assert timeouts == [60.0, 35.0, 10.0]


@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
@pytest.mark.parametrize("head_status", [200, 400, 403, 404, 405, 501])
def test_read_url_checks_get_redirect_regardless_of_head_response(head_status):
    """HEAD responses cannot predict the redirects followed by a GET request."""
    import http.server
    import threading

    from gptme.tools._url_safety import set_session_allow_hosts

    ports: list[int] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(head_status)
            self.end_headers()

        def do_GET(self):
            if self.path.startswith("/to-other"):
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{ports[0]}/secret")
                self.end_headers()
            elif self.path.startswith("/to-same"):
                self.send_response(302)
                self.send_header("Location", "/public")
                self.end_headers()
            else:
                body = b"<p>SECRET</p>" if "secret" in self.path else b"<p>PUBLIC</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    ports.append(server.server_port)
    try:
        set_session_allow_hosts(["127.0.0.1"])
        assert "PUBLIC" in read_url(f"http://127.0.0.1:{ports[0]}/to-same")
        with pytest.raises(ValueError, match="localhost"):
            read_url(f"http://127.0.0.1:{ports[0]}/to-other")
    finally:
        set_session_allow_hosts(None)
        server.shutdown()
        server.server_close()


def test_read_url_redirect_probes_send_cookies(mock_probe_via_run):
    """Allow-listed redirect probes carry the caller's cookies.

    Google search relies on SOCS/CONSENT cookies to avoid a consent redirect; a
    probe without them would follow that redirect instead of the search page.
    """
    from subprocess import CompletedProcess

    from gptme.tools._url_safety import set_session_allow_hosts

    probe_cookies: list[str] = []

    def mock_run(cmd, **kwargs):
        if "-mime_header" in cmd:
            cookie_arg = next((a for a in cmd if a.startswith("-cookie_file=")), None)
            assert cookie_arg is not None, f"probe without cookies: {cmd}"
            assert "-accept_all_cookies" in cmd
            with open(cookie_arg.split("=", 1)[1]) as f:
                probe_cookies.append(f.read())
            if not cmd[-1].endswith("/final"):
                return CompletedProcess(
                    cmd, 0, stdout="HTTP/1.1 302 Found\nLocation: /final\n", stderr=""
                )
            return CompletedProcess(cmd, 0, stdout="HTTP/1.1 200 OK\n", stderr="")
        assert any(a.startswith("-cookie_file=") for a in cmd)
        return CompletedProcess(cmd, 0, stdout="page", stderr="")

    try:
        set_session_allow_hosts(["www.google.com"])
        with patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run):
            assert (
                read_url("https://www.google.com/search?q=x", cookies={"SOCS": "abc"})
                == "page"
            )
    finally:
        set_session_allow_hosts(None)

    assert len(probe_cookies) == 2
    assert all("\tSOCS\tabc" in c for c in probe_cookies)


def test_read_url_cookie_file_removed_when_redirect_probe_fails(mock_probe_via_run):
    """The cookie file is cleaned up even if redirect resolution raises."""
    from subprocess import CompletedProcess

    from gptme.tools._url_safety import set_session_allow_hosts

    cookie_paths: list[str] = []

    def mock_run(cmd, **kwargs):
        cookie_paths.extend(
            a.split("=", 1)[1] for a in cmd if a.startswith("-cookie_file=")
        )
        return CompletedProcess(
            cmd, 0, stdout="HTTP/1.1 302 Found\nLocation: /loop\n", stderr=""
        )

    try:
        set_session_allow_hosts(["example.com"])
        with (
            patch("gptme.tools._browser_lynx.subprocess.run", side_effect=mock_run),
            pytest.raises(RuntimeError, match="Too many redirects"),
        ):
            read_url("https://example.com/start", cookies={"SID": "abc"})
    finally:
        set_session_allow_hosts(None)

    assert cookie_paths
    assert not any(os.path.exists(p) for p in cookie_paths)


@pytest.mark.skipif(not lynx_available, reason="lynx not installed")
def test_allowlisted_redirect_hops_carry_session_cookie():
    """A session cookie set on one hop must reach the next hop and the fetch.

    With an allowlist every hop is its own lynx process, so the cookie only
    survives if the harness writes it to the shared jar (gptme#5133 follow-up).
    """
    import http.server
    import threading

    from gptme.tools._url_safety import set_session_allow_hosts

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/account/start":
                self.send_response(302)
                # No Path: defaults to /account, which must match /account.
                self.send_header("Set-Cookie", "sid=abc123")
                self.send_header("Location", "/account")
                self.end_headers()
            elif "sid=abc123" in self.headers.get("Cookie", ""):
                body = b"<p>WELCOME</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                # No cookie: bounce back, like a login wall does.
                self.send_response(302)
                self.send_header("Location", "/account/start")
                self.end_headers()

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/account/start"
    try:
        # One lynx process follows the chain in-process, so cookies survive.
        assert "WELCOME" in read_url(url)

        # Per-hop probes under an allowlist only work if hops share a jar.
        set_session_allow_hosts(["127.0.0.1"])
        assert "WELCOME" in read_url(url)
    finally:
        set_session_allow_hosts(None)
        server.shutdown()
        server.server_close()


def _absorb(headers: list[str], url: str, jar_dir, seed: dict | None = None):
    """Feed Set-Cookie headers into a jar and return ``{(domain, path, name): value}``."""
    from gptme.tools._browser_lynx import _CookieJar

    jar = _CookieJar(str(jar_dir / "jar.txt"), str(jar_dir / "jar.cfg"))
    jar.entries.update(seed or {})
    head = "HTTP/1.1 302 Found\r\n" + "".join(f"Set-Cookie: {h}\r\n" for h in headers)
    jar.absorb(head + "\r\n", url)
    return {key: entry[0] for key, entry in jar.entries.items()}


@pytest.mark.parametrize(
    ("url", "domain", "expected"),
    [
        ("https://a.example.com/", "example.com", ".example.com"),
        ("https://a.example.com/", ".Example.com", ".example.com"),
        ("https://example.com/", "example.com", ".example.com"),
        ("https://localhost/", "localhost", "localhost"),
        ("https://127.0.0.1/", "127.0.0.1", "127.0.0.1"),
        # Not the sender's own host or a parent of it.
        ("https://a.example/", "b.example", None),
        ("https://evilexample.com/", "example.com", None),
        ("https://example.com/", "a.example.com", None),
        # Suffixes shared between unrelated owners, and IP "parents".
        ("https://a.example.com/", "com", None),
        ("https://a.example.co.uk/", "co.uk", None),
        ("https://127.0.0.1/", "0.0.1", None),
    ],
)
def test_cookie_domain_is_limited_to_sending_host(tmp_path, url, domain, expected):
    stored = _absorb([f"sid=x; Domain={domain}; Path=/"], url, tmp_path)

    assert stored == ({(expected, "/", "sid"): "x"} if expected else {})


@pytest.mark.parametrize(
    "deletion",
    [
        "sid=gone; Path=/; Max-Age=0",
        "sid=; Path=/",
        "sid=gone; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "sid=gone; Path=/; Domain=example.com; Max-Age=-1",
    ],
)
def test_cookie_deletion_removes_stored_cookie(tmp_path, deletion):
    # The seed has the tail-matching form used for caller-supplied cookies.
    seed = {(".example.com", "/", "sid"): ("old", 2**33, False)}

    assert _absorb([deletion], "https://example.com/", tmp_path, seed) == {}
    assert "old" not in (tmp_path / "jar.txt").read_text()


def test_cookie_deletion_cannot_reach_unrelated_domain(tmp_path):
    seed = {(".b.example", "/", "sid"): ("keep", 2**33, False)}

    stored = _absorb(
        ["sid=; Domain=b.example; Path=/; Max-Age=0"],
        "https://a.example/",
        tmp_path,
        seed,
    )

    assert stored == {(".b.example", "/", "sid"): "keep"}


def test_subdomain_cannot_delete_parent_host_only_cookie(tmp_path):
    seed = {("example.com", "/", "sid"): ("keep", 2**33, False)}

    stored = _absorb(
        ["sid=; Domain=example.com; Path=/; Max-Age=0"],
        "https://a.example.com/",
        tmp_path,
        seed,
    )

    assert stored == {("example.com", "/", "sid"): "keep"}


@pytest.mark.parametrize(
    ("request_path", "cookie_path"),
    [
        ("", "/"),
        ("/login", "/"),
        ("/account/login", "/account"),
        ("/account/", "/account"),
    ],
)
def test_cookie_default_path(tmp_path, request_path, cookie_path):
    url = f"https://example.com{request_path}"

    assert _absorb(["sid=x"], url, tmp_path) == {
        ("example.com", cookie_path, "sid"): "x"
    }


@pytest.mark.parametrize(
    "attributes",
    [
        # Max-Age wins in either order.
        "Max-Age=3600; Expires=Thu, 01 Jan 1970 00:00:00 GMT",
        "Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=3600",
        # Unreadable attributes are ignored.
        "Max-Age=--1",
        "Max-Age=\u00b2",
        "Max-Age=" + "9" * 5000,
        "Expires=Thu, 01 Jan 99999999 00:00:00 GMT",
        "Expires=not a date",
    ],
)
def test_cookie_lifetime_attributes(tmp_path, attributes):
    stored = _absorb([f"sid=x; {attributes}"], "https://example.com/", tmp_path)

    assert stored == {("example.com", "/", "sid"): "x"}
