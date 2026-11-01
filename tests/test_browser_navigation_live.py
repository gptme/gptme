"""Offline public-wrapper navigation with real Chromium and synthetic auth only."""

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright

from gptme.tools import _browser_playwright as pw
from gptme.tools import _browser_thread as bt
from gptme.tools import browser as tool
from gptme.tools._url_safety import set_session_allow_hosts


@pytest.fixture
def auth_server() -> Iterator[tuple[str, list[tuple[str, str, str]]]]:
    requests: list[tuple[str, str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/favicon.ico":
                requests.append(
                    (
                        self.path,
                        self.headers.get("Host", ""),
                        self.headers.get("Cookie", ""),
                    )
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path == "/login":
                self.send_header(
                    "Set-Cookie", "fixture_auth=live; Path=/; SameSite=Lax"
                )
            self.end_headers()
            body = "<title>Account</title><a href='/account'>Account</a>"
            if self.path == "/login":
                body += "<script>localStorage.setItem('fixture_auth', 'live')</script>"
            body += "<p id='state'></p><script>document.querySelector('#state').textContent = localStorage.getItem('fixture_auth') || 'no local auth'</script>"
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def live_browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[bt.BrowserThread]:
    with sync_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        if not executable.exists():
            candidates = sorted(
                Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux*/chrome")
            )
            if not candidates:
                pytest.skip("Playwright Chromium not installed")
            executable = candidates[-1]
    # No production profile, configured state file, CDP endpoint or browser instance.
    # Preserve real storage-state override handling, but exclude all inherited
    # environment/chat/project/user browser settings (including bare CDP URLs).
    monkeypatch.setattr(
        bt, "get_config", lambda: SimpleNamespace(get_env=lambda key: None)
    )
    monkeypatch.setattr(bt, "_override_storage_state", None)
    monkeypatch.setattr(pw, "_browser", None)
    monkeypatch.setattr(pw, "_current_page", None)
    monkeypatch.setattr(pw, "_current_context", None)
    monkeypatch.setattr(tool, "browser", "playwright")

    worker = bt.BrowserThread(engine="chromium", executable_path=str(executable))
    monkeypatch.setattr(pw, "_browser", worker)
    try:
        yield worker
    finally:
        if worker.thread.is_alive():
            worker.execute(pw._do_close_page)
            worker.stop()
        set_session_allow_hosts(None)


def test_public_direct_navigation_retains_live_auth(
    auth_server: tuple[str, list[tuple[str, str, str]]],
    live_browser: bt.BrowserThread,
) -> None:
    base, requests = auth_server
    tool.open_page(base + "/login")
    # Same-tab link is the control; then navigate directly via the public wrapper.
    tool.click_element("a")
    assert "live" in tool.read_page_text()
    snapshot = tool.open_page(base + "/direct-account")
    assert "live" in snapshot
    assert requests[-1][0] == "/direct-account"
    assert "fixture_auth=live" in requests[-1][2]

    def state(browser: object) -> str | None:
        assert pw._current_page is not None
        return pw._current_page.evaluate("localStorage.getItem('fixture_auth')")

    assert live_browser.execute(state) == "live"
    tool.close_page()
    tool.open_page(base + "/fresh-account")
    assert requests[-1][2] == ""
    assert live_browser.execute(state) is None


def test_public_navigation_keeps_origin_isolation_and_host_policy(
    auth_server: tuple[str, list[tuple[str, str, str]]],
    live_browser: bt.BrowserThread,
) -> None:
    base, requests = auth_server
    other = base.replace("127.0.0.1", "localhost")
    set_session_allow_hosts(["127.0.0.1"])
    tool.open_page(base + "/login")
    count = len(requests)
    with pytest.raises(ValueError, match="allowed-hosts"):
        tool.open_page(other + "/blocked")
    assert len(requests) == count
    # Rejected entry URLs do not destroy the previous working session.
    assert "live" in tool.open_page(base + "/still-authenticated")
    assert "fixture_auth=live" in requests[-1][2]

    set_session_allow_hosts(["127.0.0.1", "localhost"])
    assert "no local auth" in tool.open_page(other + "/other-origin")
    assert requests[-1][2] == ""
    assert "live" in tool.open_page(base + "/original-origin")
    assert "fixture_auth=live" in requests[-1][2]


def test_public_restart_drops_unsaved_live_auth(
    auth_server: tuple[str, list[tuple[str, str, str]]],
    live_browser: bt.BrowserThread,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, requests = auth_server
    tool.open_page(base + "/login")
    pw._restart_browser()
    replacement = bt.BrowserThread(
        engine="chromium", executable_path=live_browser.executable_path
    )
    monkeypatch.setattr(pw, "_browser", replacement)
    try:
        assert "no local auth" in tool.open_page(base + "/new-session")
        assert requests[-1][2] == ""
    finally:
        replacement.execute(pw._do_close_page)
        replacement.stop()


def test_public_explicit_load_replaces_live_auth(
    auth_server: tuple[str, list[tuple[str, str, str]]],
    live_browser: bt.BrowserThread,
    tmp_path: Path,
) -> None:
    base, requests = auth_server
    tool.open_page(base + "/login")
    missing = tmp_path / "missing.json"
    # Invalid-load retention beyond missing files belongs to #4646.
    with pytest.raises(FileNotFoundError):
        tool.load_browser_state(str(missing))
    assert "live" in tool.open_page(base + "/retained")
    assert "fixture_auth=live" in requests[-1][2]
    saved = tmp_path / "saved.json"
    tool.save_browser_state(str(saved))
    tool.close_page()
    assert "no local auth" in tool.open_page(base + "/fresh")
    assert requests[-1][2] == ""
    tool.load_browser_state(str(saved))
    assert "live" in tool.open_page(base + "/restored")
    assert "fixture_auth=live" in requests[-1][2]
    empty = tmp_path / "empty.json"
    empty.write_text('{"cookies": [], "origins": []}')
    tool.load_browser_state(str(empty))
    assert "no local auth" in tool.open_page(base + "/replaced")
    assert requests[-1][2] == ""


def test_public_navigation_failure_discards_owned_context(
    auth_server: tuple[str, list[tuple[str, str, str]]],
    live_browser: bt.BrowserThread,
) -> None:
    base, requests = auth_server
    tool.open_page(base + "/login")

    def abort_navigation(browser) -> None:
        assert pw._current_page is not None
        pw._current_page.route("**/failure", lambda route: route.abort())

    live_browser.execute(abort_navigation)
    with pytest.raises(RuntimeError, match="Failed to navigate"):
        tool.open_page(base + "/failure")
    assert "no local auth" in tool.open_page(base + "/after-failure")
    assert requests[-1][2] == ""
