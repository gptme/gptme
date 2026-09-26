"""Tests for the URL safety host allowlist feature."""

import pytest

from gptme.tools._url_safety import (
    _host_matches,
    _validate_entry_url,
    _validate_page_url,
    _validate_url_scheme,
    parse_allow_hosts,
    set_session_allow_hosts,
)


def teardown_function():
    """Reset allowlist after each test."""
    set_session_allow_hosts(None)


# ── _host_matches ──────────────────────────────────────────────────────────


def test_exact_match():
    assert _host_matches("github.com", "github.com") is True


def test_exact_mismatch():
    assert _host_matches("notgithub.com", "github.com") is False


def test_wildcard_subdomain_matches():
    assert _host_matches("api.github.com", "*.github.com") is True


def test_wildcard_deep_subdomain_matches():
    assert _host_matches("raw.githubusercontent.com", "*.githubusercontent.com") is True


def test_wildcard_does_not_match_base():
    # *.github.com should NOT match github.com itself
    assert _host_matches("github.com", "*.github.com") is False


def test_wildcard_does_not_match_sibling():
    assert _host_matches("notgithub.com", "*.github.com") is False


def test_wildcard_does_not_partial_match():
    assert _host_matches("evildotgithub.com", "*.github.com") is False


def test_case_insensitive_hostname():
    assert _host_matches("GitHub.com", "github.com") is True


def test_case_insensitive_pattern():
    assert _host_matches("github.com", "GitHub.com") is True


def test_case_insensitive_wildcard():
    assert _host_matches("API.GitHub.com", "*.github.com") is True


# ── _validate_url_host via _validate_url_scheme ────────────────────────────


def test_none_allowlist_unrestricted():
    set_session_allow_hosts(None)
    _validate_url_scheme("https://urlquery.net/")  # no raise


def test_host_not_in_allowlist_raises():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://urlquery.net/fetch?url=https://github.com")


def test_allowed_host_passes():
    set_session_allow_hosts(["github.com"])
    _validate_url_scheme("https://github.com/user/repo")  # no raise


def test_wildcard_subdomain_allowed():
    set_session_allow_hosts(["*.github.com"])
    _validate_url_scheme("https://api.github.com/v3/repos")  # no raise


def test_wildcard_does_not_cover_base_domain():
    set_session_allow_hosts(["*.github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://github.com/")


def test_exact_domain_no_suffix_bleed():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://notgithub.com/")


def test_multiple_allowed_hosts():
    set_session_allow_hosts(["github.com", "api.openai.com"])
    _validate_url_scheme("https://github.com/")  # no raise
    _validate_url_scheme("https://api.openai.com/")  # no raise
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://evil.com/")


def test_empty_allowlist_blocks_all_hosts():
    # An explicit empty list is "block everything", not "unrestricted".
    set_session_allow_hosts([])
    with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
        _validate_url_scheme("https://github.com/")


def test_url_with_uppercase_host_matches_lowercase_allowlist():
    set_session_allow_hosts(["github.com"])
    _validate_url_scheme("https://GitHub.com/user/repo")  # no raise


def test_allowlist_enforced_from_worker_thread():
    # BrowserThread.execute copies the caller's contextvars context and runs
    # commands inside it; the session allowlist must reach checks through that
    # copy (or the post-redirect / post-interaction checks silently no-op).
    # This exercises the real execute() path (queue + worker thread), with a
    # stubbed worker loop standing in for the playwright runner.
    import threading
    from queue import Empty, Queue
    from threading import Lock

    # _browser_thread imports playwright at module level; skip when the browser
    # extra isn't installed (the no-extras CI job has no playwright).
    bt_mod = pytest.importorskip("gptme.tools._browser_thread")
    BrowserThread = bt_mod.BrowserThread

    set_session_allow_hosts(["github.com"])

    # Build a BrowserThread without launching a real browser: stub the worker
    # loop that _run() would normally provide.
    bt = BrowserThread.__new__(BrowserThread)
    bt.queue = Queue()
    bt.results = {}
    bt.lock = Lock()

    def worker_loop() -> None:
        while True:
            try:
                cmd, cmd_id = bt.queue.get(timeout=1.0)
            except Empty:
                continue
            if cmd == "stop":
                break
            try:
                result = cmd.func(None, *cmd.args, **cmd.kwargs)
                with bt.lock:
                    bt.results[cmd_id] = (result, None)
            except Exception as e:
                with bt.lock:
                    bt.results[cmd_id] = (None, e)

    thread = threading.Thread(target=worker_loop, daemon=True)
    thread.start()
    bt.thread = thread

    # Tool functions receive the browser as their first argument; mirror that
    # calling convention here.
    check = lambda _browser, url: _validate_url_scheme(url)  # noqa: E731

    errors: list[ValueError] = []
    try:
        bt.execute(check, "https://urlquery.net/")
    except ValueError as exc:
        errors.append(exc)
    bt.execute(check, "https://github.com/ok")  # no raise
    bt.stop()
    assert errors, "allowlist must reach checks executed via BrowserThread.execute"


def test_page_url_allows_browser_internal_schemes_when_unrestricted():
    # blob:/chrome: are browser-internal or same-origin state; pages that
    # legitimately navigate there must keep working in unrestricted sessions.
    _validate_page_url("blob:https://example.com/9a3f-4c1d")
    _validate_page_url("chrome://settings/")


def test_bare_star_allowlist_entry_rejected():
    # A bare "*" is ambiguous: reject it instead of silently allowing or
    # silently blocking everything.
    with pytest.raises(ValueError, match="ambiguous"):
        parse_allow_hosts("*")


def test_data_url_blocked_when_allowlist_active():
    # data:/about: are offline content, but with an explicit allowlist the
    # strict scheme validation applies so "block all hosts" blocks everything.
    set_session_allow_hosts(["github.com"])
    with pytest.raises(ValueError, match="not allowed"):
        _validate_entry_url("data:text/html;base64,SGVsbG8=")
    with pytest.raises(ValueError, match="not allowed"):
        _validate_page_url("data:text/html,<h1>x</h1>")
    with pytest.raises(ValueError, match="not allowed"):
        _validate_page_url("about:blank")
    set_session_allow_hosts(None)
    _validate_entry_url("data:text/html;base64,SGVsbG8=")  # no raise
    _validate_page_url("about:blank")  # no raise


def test_error_message_names_host_and_list():
    set_session_allow_hosts(["github.com"])
    with pytest.raises(
        ValueError, match="not in the session's allowed-hosts"
    ) as exc_info:
        _validate_url_scheme("https://urlquery.net/")
    msg = str(exc_info.value)
    assert "urlquery.net" in msg
    assert "github.com" in msg
    assert "--allow-hosts" in msg


# ── parse_allow_hosts (the actual CLI/env-var parsing path) ────────────────


def test_parse_allow_hosts_comma_separated():
    assert parse_allow_hosts("github.com, api.github.com") == [
        "github.com",
        "api.github.com",
    ]


def test_parse_allow_hosts_single():
    assert parse_allow_hosts("github.com") == ["github.com"]


def test_parse_allow_hosts_none_is_unrestricted():
    assert parse_allow_hosts(None) is None


def test_parse_allow_hosts_empty_string_is_empty_list():
    # Must be [] (block all), not None (unrestricted): the CLI guard used to
    # coerce "" to None, silently disabling the restriction.
    assert parse_allow_hosts("") == []


def test_parse_allow_hosts_whitespace_only_is_empty_list():
    assert parse_allow_hosts("   ") == []


def test_parse_allow_hosts_lowercases():
    assert parse_allow_hosts("GitHub.com, API.OpenAI.com") == [
        "github.com",
        "api.openai.com",
    ]


def test_request_allowlisted_validates_every_redirect_hop(monkeypatch):
    """Redirects must be checked BEFORE each request, not after the fetch.

    With allow_redirects=True the disallowed host is already contacted (the
    exfiltration happens on the wire) even though the response is discarded.
    """
    from gptme.tools import browser

    requested_urls = []

    class FakeResponse:
        def __init__(self, url, redirect_to=None):
            self.url = url
            self.status_code = 302 if redirect_to else 200
            self.headers = {"Location": redirect_to} if redirect_to else {}
            self.is_redirect = bool(redirect_to)
            self.is_permanent_redirect = False

        def raise_for_status(self):
            pass

    def fake_request(method, url, timeout=None, allow_redirects=False):
        assert allow_redirects is False
        requested_urls.append(url)
        if url == "https://allowed.example.com/start":
            return FakeResponse(url, redirect_to="https://evil.example.net/exfil")
        return FakeResponse(url)

    class FakeSession:
        request = staticmethod(fake_request)

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "Session", FakeSession)
    set_session_allow_hosts(["allowed.example.com"])
    try:
        with pytest.raises(ValueError, match="not in the session's allowed-hosts"):
            browser._request_allowlisted("GET", "https://allowed.example.com/start", 10)
    finally:
        set_session_allow_hosts(None)
    # The disallowed host must never have been requested.
    assert requested_urls == ["https://allowed.example.com/start"]


def test_request_allowlisted_follows_allowed_redirects(monkeypatch):
    from gptme.tools import browser

    class FakeResponse:
        def __init__(self, url, redirect_to=None):
            self.url = url
            self.status_code = 302 if redirect_to else 200
            self.headers = {"Location": redirect_to} if redirect_to else {}
            self.is_redirect = bool(redirect_to)
            self.is_permanent_redirect = False

        def raise_for_status(self):
            pass

    urls = ["https://a.example.com/start", "https://b.example.com/end"]

    def fake_request(method, url, timeout=None, allow_redirects=False):
        next_url = (
            urls[urls.index(url) + 1] if urls.index(url) + 1 < len(urls) else None
        )
        return FakeResponse(url, redirect_to=next_url)

    class FakeSession:
        request = staticmethod(fake_request)

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "Session", FakeSession)
    set_session_allow_hosts(["a.example.com", "b.example.com"])
    try:
        resp = browser._request_allowlisted("GET", "https://a.example.com/start", 10)
    finally:
        set_session_allow_hosts(None)
    assert resp.url == "https://b.example.com/end"


def test_request_allowlisted_preserves_cookies_across_hops(monkeypatch):
    """A cookie set on the redirect response must reach the destination request.

    A PDF endpoint may set a cookie in the redirect response that the
    destination requires; per-hop standalone requests would drop it.
    """
    from gptme.tools import browser

    sent_cookies = []

    class FakeResponse:
        def __init__(self, url, redirect_to=None):
            self.url = url
            self.status_code = 302 if redirect_to else 200
            self.headers = {"Location": redirect_to} if redirect_to else {}

    class FakeCookies:
        def __init__(self):
            self._cookies: dict = {}

    class FakeSession:
        def __init__(self):
            self.cookies = FakeCookies()

        def request(self, method, url, timeout=None, allow_redirects=False):
            sent_cookies.append(dict(self.cookies._cookies))
            if url == "https://allowed.example.com/start":
                resp = FakeResponse(url, redirect_to="https://allowed.example.com/doc")
                self.cookies._cookies["session"] = "abc"
                return resp
            return FakeResponse(url)

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "Session", FakeSession)
    set_session_allow_hosts(["allowed.example.com"])
    try:
        browser._request_allowlisted("GET", "https://allowed.example.com/start", 10)
    finally:
        set_session_allow_hosts(None)
    assert sent_cookies == [{}, {"session": "abc"}]


def test_request_allowlisted_tenth_hop_response_is_checked(monkeypatch):
    """A response after exactly _MAX_REDIRECT_HOPS redirects must be returned,
    not rejected as 'too many redirects'."""
    from gptme.tools import browser
    from gptme.tools.browser import _MAX_REDIRECT_HOPS

    calls = []

    class FakeResponse:
        def __init__(self, url, redirect_to=None):
            self.url = url
            self.status_code = 302 if redirect_to else 200
            self.headers = {"Location": redirect_to} if redirect_to else {}

    class FakeSession:
        def request(self, method, url, timeout=None, allow_redirects=False):
            calls.append(url)
            # Redirect on every request except the one after the max hops.
            redirect_to = (
                f"https://allowed.example.com/hop{len(calls)}"
                if len(calls) <= _MAX_REDIRECT_HOPS
                else None
            )
            return FakeResponse(url, redirect_to=redirect_to)

        def close(self):
            pass

    monkeypatch.setattr(browser.requests, "Session", FakeSession)
    set_session_allow_hosts(["allowed.example.com"])
    try:
        resp = browser._request_allowlisted(
            "GET", "https://allowed.example.com/start", 10
        )
    finally:
        set_session_allow_hosts(None)
    assert resp.status_code == 200
    assert len(calls) == _MAX_REDIRECT_HOPS + 1
