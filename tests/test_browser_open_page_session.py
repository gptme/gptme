"""Live open_page() navigation must keep interactive authentication state.

Issue #4720: a second direct open_page() URL used to close the owned browser
context and drop cookies/localStorage from an interactive login. Explicit
close_page() still resets the session.

These tests use a loopback HTTP fixture and isolated headless Chromium. No
external site, production profile, or real credentials.
"""

from __future__ import annotations

import http.server
import threading
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

pytest.importorskip("playwright")


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _chromium_ok() -> bool:
    if not _playwright_available():
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _chromium_or_skip():
    if not _playwright_available():
        pytest.skip("playwright not installed")
    if not _chromium_ok():
        pytest.skip(
            "Playwright chromium not installed (run: playwright install chromium)"
        )


@pytest.fixture(autouse=True)
def _isolated_browser_env(monkeypatch):
    """Use Playwright's bundled Chromium, not a host profile or custom binary."""
    for key in (
        "GPTME_BROWSER_EXECUTABLE_PATH",
        "GPTME_BROWSER_CDP_URL",
        "GPTME_BROWSER_STORAGE_STATE",
        "GPTME_BROWSER_ENGINE",
        "BROWSER_EXECUTABLE_PATH",
        "BROWSER_CDP_URL",
        "BROWSER_STORAGE_STATE",
        "BROWSER_ENGINE",
    ):
        monkeypatch.delenv(key, raising=False)
    from gptme.tools._browser_playwright import _restart_browser

    _restart_browser()
    yield
    try:
        from gptme.tools._browser_playwright import close_page as _close

        _close()
    except Exception:
        pass
    _restart_browser()


_LOGIN_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Login</title></head>
<body>
<h1>Login complete</h1>
<p id="status">logged-in</p>
<script>
  localStorage.setItem("auth", "live-token");
  document.getElementById("status").textContent = "logged-in localstorage-set";
</script>
</body>
</html>
"""

_ACCOUNT_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Account</title></head>
<body>
<h1>Account</h1>
<p id="cookie">COOKIE_PLACEHOLDER</p>
<p id="storage">checking-localstorage</p>
<script>
  const token = localStorage.getItem("auth");
  document.getElementById("storage").textContent =
    token === "live-token" ? "localstorage-ok" : "localstorage-missing";
</script>
</body>
</html>
"""


class _AuthHandler(http.server.BaseHTTPRequestHandler):
    """Loopback login/account fixture with a synthetic auth cookie."""

    last_account_cookie: str | None = None

    def log_message(self, *args):  # suppress request logs in test output
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/login":
            body = _LOGIN_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", "auth=live-session; Path=/")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/account":
            cookie = self.headers.get("Cookie", "")
            _AuthHandler.last_account_cookie = cookie
            marker = "cookie-ok" if "auth=live-session" in cookie else "cookie-missing"
            html = _ACCOUNT_HTML.replace("COOKIE_PLACEHOLDER", marker)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture()
def auth_server() -> Generator[str, None, None]:
    _AuthHandler.last_account_cookie = None
    server = http.server.HTTPServer(("127.0.0.1", 0), _AuthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=2)


@pytest.mark.integration
def test_direct_open_page_keeps_live_auth_state(auth_server: str):
    """A second direct open_page() must keep cookies and localStorage."""
    from gptme.tools.browser import open_page

    login = open_page(f"{auth_server}/login")
    assert "logged-in" in login

    account = open_page(f"{auth_server}/account")
    assert "cookie-ok" in account, (
        f"direct open_page discarded the auth cookie; server Cookie="
        f"{_AuthHandler.last_account_cookie!r}\n{account[:500]}"
    )
    assert "localstorage-ok" in account, (
        f"direct open_page discarded localStorage\n{account[:500]}"
    )


@pytest.mark.integration
def test_close_page_resets_live_auth_state(auth_server: str):
    """Explicit close_page() must still drop the interactive session."""
    from gptme.tools.browser import close_page, open_page

    open_page(f"{auth_server}/login")
    close_page()

    account = open_page(f"{auth_server}/account")
    assert "cookie-missing" in account, (
        f"close_page() should reset cookies; server Cookie="
        f"{_AuthHandler.last_account_cookie!r}\n{account[:500]}"
    )
    assert "localstorage-missing" in account, (
        f"close_page() should reset localStorage\n{account[:500]}"
    )
