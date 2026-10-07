"""Browser-agent blockage conformance suite.

Five local synthetic fixtures that document how the current gptme browser stack
behaves when it encounters common web blockage patterns.  The suite intentionally
runs offline — no live sites — so it stays stable across network conditions and
site changes.

Baseline established: 2026-10-05 (gptme issue #4199).

Patterns covered:
1. Login wall        — page body is only an auth form
2. Consent overlay   — full-screen div hides content
3. Rate limit        — HTTP 429 with Retry-After header
4. Bot challenge     — Cloudflare-style JS challenge page
5. Misleading SPA    — page shows only a loading spinner
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest

playwright = pytest.importorskip("playwright")

from gptme.tools.browser import read_url, snapshot_url

# ---------------------------------------------------------------------------
# Local HTTP server fixture
# ---------------------------------------------------------------------------

_PAGES: dict[str, tuple[int, str, str]] = {}  # path -> (status, content_type, body)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # suppress request logs in test output
        pass

    def do_GET(self):
        entry = _PAGES.get(self.path)
        if entry is None:
            self.send_response(404)
            self.end_headers()
            return
        status, ctype, body = entry
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(encoded)))
        if status == 429:
            self.send_header("Retry-After", "60")
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture(scope="module")
def blockage_server() -> Iterator[str]:
    """Start a module-scoped local HTTP server serving the blockage pages."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    _PAGES["/login-wall"] = (
        200,
        "text/html",
        """<!DOCTYPE html>
<html><head><title>Sign In Required</title></head>
<body>
  <h1>Sign In</h1>
  <form method="post" action="/auth">
    <label>Email: <input type="email" name="email"></label>
    <label>Password: <input type="password" name="password"></label>
    <button type="submit">Sign In</button>
  </form>
</body></html>""",
    )

    _PAGES["/consent-overlay"] = (
        200,
        "text/html",
        """<!DOCTYPE html>
<html><head><title>News Article</title></head>
<body>
  <div id="overlay" style="position:fixed;top:0;left:0;width:100%;height:100%;
       background:#fff;z-index:9999;display:flex;align-items:center;justify-content:center">
    <div>
      <h2>We value your privacy</h2>
      <p>Accept cookies to continue reading.</p>
      <button id="accept">Accept All</button>
      <button id="reject">Reject</button>
    </div>
  </div>
  <article id="content" style="visibility:hidden">
    <h1>Secret Article Title</h1>
    <p>This is the actual article body text you wanted to read.</p>
  </article>
</body></html>""",
    )

    _PAGES["/rate-limited"] = (
        429,
        "text/html",
        """<!DOCTYPE html>
<html><head><title>Too Many Requests</title></head>
<body><h1>429 Too Many Requests</h1>
<p>You have sent too many requests. Please wait 60 seconds before trying again.</p>
</body></html>""",
    )

    _PAGES["/bot-challenge"] = (
        200,
        "text/html",
        """<!DOCTYPE html>
<html><head><title>Just a moment...</title></head>
<body>
  <div id="challenge-stage">
    <h1>Checking your browser</h1>
    <p>One more step — please complete the security check to proceed.</p>
    <div class="challenge-spinner"></div>
    <p id="please-wait">Please wait while we verify you are human...</p>
  </div>
  <noscript>Please enable JavaScript to continue.</noscript>
</body></html>""",
    )

    _PAGES["/spa-loading"] = (
        200,
        "text/html",
        """<!DOCTYPE html>
<html><head><title>App</title></head>
<body>
  <div id="app">
    <div class="loading-spinner" aria-label="Loading...">
      <div class="spinner-icon"></div>
      <p>Loading, please wait...</p>
    </div>
  </div>
  <script>
    // In a real SPA, this script would fetch data and replace the spinner.
    // Here it never resolves to simulate an infinite loading state.
  </script>
</body></html>""",
    )

    yield base
    server.shutdown()
    thread.join(timeout=2)


# ---------------------------------------------------------------------------
# 1. Login wall
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_login_wall_read_url_surfaces_auth_form(blockage_server):
    """read_url on a login wall should surface the authentication boundary."""
    result = read_url(f"{blockage_server}/login-wall")
    assert result, "read_url returned empty string"
    assert any(kw in result.lower() for kw in ("sign in", "email", "password")), (
        f"Login form text not present in result. Got:\n{result[:500]}"
    )


@pytest.mark.slow
def test_login_wall_snapshot_reflects_form(blockage_server):
    """ARIA snapshot of a login wall should reflect the form structure."""
    result = snapshot_url(f"{blockage_server}/login-wall")
    assert result, "snapshot_url returned empty string"
    # The page title or a form-related label should be visible
    assert any(
        kw in result.lower() for kw in ("sign in", "email", "password", "form", "login")
    ), f"Snapshot did not reflect login form. Got:\n{result[:500]}"


# ---------------------------------------------------------------------------
# 2. Consent overlay
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_consent_overlay_surfaces_overlay(blockage_server):
    """A full-screen consent overlay should be visible to the caller."""
    result = read_url(f"{blockage_server}/consent-overlay")
    assert result, "read_url returned empty string"
    assert any(
        kw in result.lower()
        for kw in ("privacy", "cookie", "accept", "consent", "overlay")
    ), f"Consent overlay text not present in result. Got:\n{result[:500]}"


@pytest.mark.slow
@pytest.mark.xfail(
    reason="read_url extracts visibility:hidden content behind a consent overlay",
    raises=AssertionError,
    strict=True,
)
def test_consent_overlay_does_not_expose_hidden_article(blockage_server):
    """Hidden article text should not be returned through a consent overlay."""
    result = read_url(f"{blockage_server}/consent-overlay")
    assert "Secret Article Title" not in result
    assert "This is the actual article body text you wanted to read." not in result


# ---------------------------------------------------------------------------
# 3. Rate limit (HTTP 429)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_rate_limit_response_surfaces_429_signal(blockage_server):
    """The current reader should surface the rendered HTTP 429 signal."""
    result = read_url(f"{blockage_server}/rate-limited")
    # The browser reader currently returns the rendered 429 page. If that
    # contract changes to raising an HTTP-specific error, this test should be
    # updated to assert that exact error rather than accepting every exception.
    assert result, "read_url returned empty string on 429 — no signal at all"
    rate_signal = any(
        kw in result.lower()
        for kw in ("429", "too many", "rate limit", "retry", "wait")
    )
    assert rate_signal, (
        f"read_url on 429 returned content with no rate-limit signal.\n"
        f"Got:\n{result[:500]}"
    )


# ---------------------------------------------------------------------------
# 4. Bot challenge (Cloudflare-style)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_bot_challenge_surfaces_blocking_signal(blockage_server):
    """The current reader should surface a bot-challenge indicator."""
    result = read_url(f"{blockage_server}/bot-challenge")
    assert result, "read_url returned empty string on bot-challenge page"
    # The challenge text must be visible so the LLM knows it was blocked
    assert any(
        kw in result.lower()
        for kw in (
            "checking",
            "moment",
            "verify",
            "human",
            "security check",
            "please wait",
        )
    ), f"Bot challenge page returned with no blocking indicator.\nGot:\n{result[:500]}"


# ---------------------------------------------------------------------------
# 5. Misleading SPA (infinite loading spinner)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_spa_loading_surfaces_loading_state(blockage_server):
    """A static unresolved SPA shell should surface its loading state."""
    result = read_url(f"{blockage_server}/spa-loading")
    assert result, (
        "read_url returned empty string on infinite spinner — "
        "silent failure gives no signal to the LLM"
    )
    # The loading indicator should be surfaced
    loading_signal = any(
        kw in result.lower() for kw in ("loading", "please wait", "spinner")
    )
    assert loading_signal, (
        f"SPA spinner page returned content with no loading indicator.\n"
        f"Got:\n{result[:500]}"
    )
