"""Integration test for the "Can it Tweet?" computer-use pipeline (issue #216).

Validates the complete structured-first web interaction flow:
  open_page() → fill_element() → click_element() → read_page_text()

This is the same pipeline that would be used for "Can it Tweet?" — replace the local
form server with Twitter's compose UI and the steps are identical.  We use a local
HTTP form server so tests are hermetic (no external services, no credentials, no
anti-bot detection).

Run manually (requires Playwright chromium):
    pytest tests/test_computer_use_integration.py -v

Marked ``integration`` and skipped automatically when Playwright / chromium is
not installed, so they never block CI in environments without a browser.
"""

from __future__ import annotations

import base64
import http.server
import threading
import urllib.parse
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

# ---------------------------------------------------------------------------
# Playwright availability guard
# ---------------------------------------------------------------------------


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _chromium_ok() -> bool:
    """Check if chromium is available by launching a headless instance.

    Do NOT call at module level — use the ``_chromium_or_skip`` fixture so this
    only runs during test execution (not ``pytest --collect-only``).
    """
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
    """Skip integration tests when Playwright chromium is not installed.

    Uses a fixture (not a module-level skipif marker) so the Chromium launch
    only happens during test execution, not during ``pytest --collect-only``
    or on unrelated test discovery runs.
    """
    if not _playwright_available():
        pytest.skip("playwright not installed")
    if not _chromium_ok():
        pytest.skip(
            "Playwright chromium not installed (run: playwright install chromium)"
        )


# ---------------------------------------------------------------------------
# Local HTTP form server
# ---------------------------------------------------------------------------

_FORM_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Test Form</title></head>
<body>
<h1>Test Tweet Form</h1>
<form method="POST" action="/submit">
  <label for="message">Message:</label>
  <input id="message" name="message" type="text" />
  <label for="author">Author:</label>
  <input id="author" name="author" type="text" />
  <label for="category">Category:</label>
  <select id="category" name="category">
    <option value="tech">Tech</option>
    <option value="news">News</option>
    <option value="other">Other</option>
  </select>
  <button type="submit" id="submit-btn">Post Tweet</button>
</form>
<div id="dynamic-content" style="display:none">Dynamic content loaded!</div>
<button id="show-dynamic" onclick="document.getElementById('dynamic-content').style.display='block'">
  Show Dynamic
</button>
</body>
</html>
"""

_SUBMIT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Submitted</title></head>
<body>
<h1>Posted!</h1>
<p id="result">MESSAGE_PLACEHOLDER AUTHOR_PLACEHOLDER</p>
</body>
</html>
"""


class _FormHandler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler for the tweet-form test server."""

    submitted: dict[str, str] = {}

    def log_message(self, *args):  # suppress request logs in test output
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/form":
            self._send_html(_FORM_HTML, 200)
        else:
            self._send_html("<h1>404</h1>", 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = dict(urllib.parse.parse_qsl(body))
        _FormHandler.submitted.update(params)
        # str.replace is safe with user input — str.format would crash on {/} in values.
        html = _SUBMIT_HTML_TEMPLATE.replace(
            "MESSAGE_PLACEHOLDER", params.get("message", "")
        ).replace("AUTHOR_PLACEHOLDER", params.get("author", ""))
        self._send_html(html, 200)

    def _send_html(self, html: str, status: int) -> None:
        encoded = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture()
def form_server() -> Generator[str, None, None]:
    """Start the local form server and yield its base URL."""
    _FormHandler.submitted.clear()
    server = http.server.HTTPServer(("127.0.0.1", 0), _FormHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Helpers that reset browser state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_browser_state():
    """Ensure the Playwright browser thread is reset between integration tests."""
    yield
    try:
        from gptme.tools._browser_playwright import close_page as _close

        _close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_open_page_returns_aria_snapshot(form_server: str):
    """open_page() should return a non-empty ARIA snapshot of the form page."""
    from gptme.tools.browser import open_page

    snapshot = open_page(f"{form_server}/")
    assert snapshot, "open_page() returned empty string"
    # The ARIA tree should mention form elements
    assert any(
        keyword in snapshot.lower()
        for keyword in ("input", "button", "form", "textbox")
    ), f"Expected form elements in ARIA snapshot, got:\n{snapshot[:500]}"


@pytest.mark.integration
def test_fill_element_updates_snapshot(form_server: str):
    """fill_element() should fill a field and return an updated ARIA snapshot."""
    from gptme.tools.browser import fill_element, open_page

    open_page(f"{form_server}/")
    result = fill_element("#message", "Hello, world!")
    # The returned snapshot should reflect the page is still open
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.integration
def test_can_it_tweet_full_pipeline(form_server: str):
    """End-to-end 'Can it Tweet?' pipeline.

    Validates open_page → fill_element (×2) → click_element → read_page_text
    — the same sequence the computer-use profile uses for Twitter-style submission.
    """
    from gptme.tools.browser import (
        click_element,
        fill_element,
        open_page,
        read_page_text,
    )

    # Step 1: open the form page
    snapshot = open_page(f"{form_server}/")
    assert snapshot, "open_page failed"

    # Step 2: fill message and author fields
    fill_element("#message", "Hello from gptme!")
    fill_element("#author", "TestUser")

    # Step 3: click the submit button (waits for page load internally)
    click_element("#submit-btn")

    # Step 4: read the result page
    text = read_page_text()
    assert text, "read_page_text() returned empty string after form submit"

    # The response page should echo back the submitted values
    assert "Posted!" in text or "Hello from gptme" in text or "TestUser" in text, (
        f"Submitted values not reflected in result page:\n{text[:500]}"
    )

    # Also verify the server received the POST
    assert _FormHandler.submitted.get("message") == "Hello from gptme!", (
        f"Server did not receive expected message. Received: {_FormHandler.submitted}"
    )
    assert _FormHandler.submitted.get("author") == "TestUser", (
        f"Server did not receive expected author. Received: {_FormHandler.submitted}"
    )


@pytest.mark.integration
def test_click_element_navigates(form_server: str):
    """click_element() on a submit button should navigate to the response page."""
    from gptme.tools.browser import (
        click_element,
        fill_element,
        open_page,
        read_page_text,
    )

    open_page(f"{form_server}/")
    fill_element("#message", "navigation test")
    click_element("#submit-btn")

    # After click, read_page_text should see the response page
    text = read_page_text()
    assert "Posted!" in text or "navigation test" in text, (
        f"Expected response page after click, got:\n{text[:500]}"
    )


@pytest.mark.integration
def test_snapshot_url_reads_form(form_server: str):
    """snapshot_url() (one-shot, no state) should read the ARIA tree of the form page."""
    from gptme.tools.browser import snapshot_url

    result = snapshot_url(f"{form_server}/")
    assert result, "snapshot_url() returned empty string"
    assert any(
        keyword in result.lower() for keyword in ("input", "button", "form", "textbox")
    ), f"Expected form elements in ARIA snapshot:\n{result[:500]}"


@pytest.mark.integration
def test_observe_web_returns_structured_result(form_server: str):
    """observe_web() should return ARIA-structured content with no screenshot required."""
    from gptme.tools.computer import observe_web

    msgs = observe_web(f"{form_server}/")
    assert msgs, "observe_web() returned empty list"
    combined = " ".join(
        m.content if isinstance(m.content, str) else str(m.content) for m in msgs
    )
    assert combined.strip(), "observe_web() returned messages with no content"


@pytest.mark.integration
def test_press_key_returns_snapshot(form_server: str):
    """press_key() should not raise and should return a page snapshot."""
    from gptme.tools.browser import open_page, press_key

    open_page(f"{form_server}/")
    # Tab between form fields — a benign key press that won't navigate away
    result = press_key("Tab")
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.integration
def test_press_key_waits_for_navigation_after_enter(form_server: str):
    """press_key("Enter") should return the post-submit page snapshot."""
    from gptme.tools.browser import fill_element, open_page, press_key

    open_page(f"{form_server}/")
    fill_element("#message", "enter submit test")
    result = press_key("Enter")

    assert "Posted!" in result
    assert _FormHandler.submitted.get("message") == "enter submit test"


@pytest.mark.integration
def test_select_option_picks_dropdown_value(form_server: str):
    """select_option() should pick an option from a <select> element."""
    from gptme.tools.browser import (
        click_element,
        fill_element,
        open_page,
        select_option,
    )

    open_page(f"{form_server}/")
    fill_element("#message", "dropdown test")
    select_option("#category", "news")  # select the "news" option
    click_element("#submit-btn")

    assert _FormHandler.submitted.get("category") == "news", (
        f"Expected category=news, got: {_FormHandler.submitted}"
    )


@pytest.mark.integration
def test_select_option_picks_dropdown_label_text(form_server: str):
    """select_option() should fall back to selecting by visible option label."""
    from gptme.tools.browser import (
        click_element,
        fill_element,
        open_page,
        select_option,
    )

    open_page(f"{form_server}/")
    fill_element("#message", "dropdown label test")
    select_option("#category", "News")
    click_element("#submit-btn")

    assert _FormHandler.submitted.get("category") == "news", (
        f"Expected category=news from visible label, got: {_FormHandler.submitted}"
    )


@pytest.mark.integration
def test_wait_for_element_finds_visible_element(form_server: str):
    """wait_for_element() should resolve immediately for already-visible elements."""
    from gptme.tools.browser import open_page, wait_for_element

    snapshot = open_page(f"{form_server}/")
    assert snapshot

    # The submit button is visible immediately — wait_for_element should not timeout
    result = wait_for_element("#submit-btn", timeout_ms=3000)
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.integration
def test_wait_for_element_finds_dynamically_shown_element(form_server: str):
    """wait_for_element() should find an element revealed by a click."""
    from gptme.tools.browser import click_element, open_page, wait_for_element

    open_page(f"{form_server}/")
    # The dynamic content is hidden initially; clicking the button shows it
    click_element("#show-dynamic")
    result = wait_for_element("#dynamic-content", timeout_ms=3000)
    assert result is not None
    assert "Dynamic content" in result


@pytest.mark.integration
def test_wait_for_element_raises_on_missing(form_server: str):
    """wait_for_element() should raise RuntimeError when the element never appears."""
    import pytest

    from gptme.tools.browser import open_page, wait_for_element

    open_page(f"{form_server}/")
    with pytest.raises(RuntimeError, match="did not appear"):
        wait_for_element("#nonexistent-element-xyz", timeout_ms=500)


# ---------------------------------------------------------------------------
# hover_element tests (issue #216, PR #3104)
# ---------------------------------------------------------------------------

# Hover fixture: mouseover on the trigger WRITES text into an empty div via JS.
# Uses base64 encoding (not percent-encoding) so the marker string
# "hover-revealed-dynamically" does not appear verbatim in the URL — the
# open_page() ARIA snapshot includes the URL, so a percent-encoded URL would
# contain the marker even before the hover fires.
_HOVER_FIXTURE_HTML = (
    "<!doctype html><html><body>"
    '<div id="trigger" style="cursor:pointer">Hover me</div>'
    '<div id="revealed"></div>'
    "<script>"
    "document.getElementById('trigger').addEventListener('mouseover', function() {"
    "document.getElementById('revealed').textContent = 'hover-revealed-dynamically';"
    "});"
    "</script>"
    "</body></html>"
)
_HOVER_FIXTURE_URL = "data:text/html;base64," + base64.b64encode(
    _HOVER_FIXTURE_HTML.encode()
).decode("ascii")


@pytest.mark.integration
def test_hover_element_reveals_hidden_content():
    """hover_element() should trigger mouseover and reveal hidden content in ARIA snapshot.

    The fixture starts with an empty #revealed div — empty elements are omitted
    from the ARIA accessibility tree.  The JS mouseover handler writes text into
    it, making it appear in the ARIA snapshot.  We compare snapshots before and
    after to verify the hover event fired.
    """
    from gptme.tools.browser import hover_element, open_page, snapshot_page

    snapshot_before = open_page(_HOVER_FIXTURE_URL)
    # The target div is empty in source HTML — not in ARIA snapshot before hover
    assert "hover-revealed-dynamically" not in snapshot_before, (
        "hover-revealed-dynamically should not appear before hovering\n"
        f"snapshot: {snapshot_before[:300]}"
    )

    # hover_element() fires the mouseover handler, which writes text into #revealed
    result = hover_element("#trigger")
    assert result is not None
    assert isinstance(result, str)

    # After hover the JS textContent write makes the text appear in ARIA snapshot
    snapshot_after = snapshot_page()
    assert "hover-revealed-dynamically" in snapshot_after, (
        f"hover_element() should have written text into #revealed:\n{snapshot_after[:500]}"
    )


@pytest.mark.integration
def test_hover_element_returns_aria_snapshot():
    """hover_element() should return a non-empty ARIA snapshot after the hover."""
    from gptme.tools.browser import hover_element, open_page

    open_page(_HOVER_FIXTURE_URL)
    snapshot = hover_element("#trigger")
    assert snapshot, "hover_element() returned empty string"
    assert isinstance(snapshot, str)


# ---------------------------------------------------------------------------
# snapshot_page tests (issue #216, PR #3104)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_snapshot_page_reflects_filled_field(form_server: str):
    """snapshot_page() should capture current DOM state after fill_element()."""
    from gptme.tools.browser import fill_element, open_page, snapshot_page

    open_page(f"{form_server}/")
    fill_element("#message", "snapshot-test-value")

    # snapshot_page() reads current page state, including filled fields
    snapshot = snapshot_page()
    assert snapshot, "snapshot_page() returned empty string"
    assert isinstance(snapshot, str)
    # The ARIA snapshot should reflect the current page structure
    assert any(
        keyword in snapshot.lower()
        for keyword in ("input", "form", "button", "textbox")
    ), f"snapshot_page() should include form elements, got:\n{snapshot[:500]}"
    assert "snapshot-test-value" in snapshot, (
        f"snapshot_page() should reflect the filled value, got:\n{snapshot[:500]}"
    )


@pytest.mark.integration
def test_snapshot_page_raises_without_open_page():
    """snapshot_page() should raise RuntimeError when no page is open."""
    from gptme.tools._browser_playwright import close_page
    from gptme.tools.browser import snapshot_page

    close_page()  # ensure no page is open

    with pytest.raises(RuntimeError):
        snapshot_page()


# ---------------------------------------------------------------------------
# get_current_url tests (issue #216, PR #3104)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_get_current_url_returns_opened_url(form_server: str):
    """get_current_url() should return the URL of the currently open page."""
    from gptme.tools.browser import get_current_url, open_page

    open_page(f"{form_server}/")
    url = get_current_url()
    assert url, "get_current_url() returned empty string"
    assert "127.0.0.1" in url or "localhost" in url, (
        f"Expected URL to contain the form server host, got: {url!r}"
    )


@pytest.mark.integration
def test_get_current_url_updates_after_navigation(form_server: str):
    """get_current_url() should reflect the new URL after navigation."""
    from gptme.tools.browser import (
        click_element,
        fill_element,
        get_current_url,
        open_page,
    )

    open_page(f"{form_server}/")
    url_before = get_current_url()

    # Submit the form — the browser navigates to the form's action URL (/submit)
    fill_element("#message", "navigation-url-test")
    click_element("#submit-btn")

    url_after = get_current_url()
    assert url_after, "get_current_url() returned empty string after navigation"
    # URL should have changed after form submission navigated to /submit
    assert url_after != url_before, (
        f"URL should change after navigation: before={url_before!r}, after={url_after!r}"
    )


# ---------------------------------------------------------------------------
# save_browser_state / load_browser_state tests (issue #216)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_save_browser_state_creates_file(tmp_path: Path, form_server: str):
    """save_browser_state() should write a JSON session file to disk."""
    import json

    from gptme.tools.browser import open_page, save_browser_state

    open_page(f"{form_server}/")
    state_path = str(tmp_path / "session.json")
    result = save_browser_state(state_path)

    # Returns a confirmation string mentioning the path
    assert result, "save_browser_state() returned empty string"
    assert str(tmp_path) in result or "session.json" in result, (
        f"Confirmation should mention the path, got: {result!r}"
    )

    # The file must exist and be valid JSON
    import os

    assert os.path.exists(state_path), f"State file not created at {state_path}"
    with open(state_path) as f:
        state = json.load(f)
    # Playwright storage state always has "cookies" and "origins" keys
    assert "cookies" in state, (
        f"Storage state JSON missing 'cookies' key: {list(state.keys())}"
    )


@pytest.mark.integration
def test_load_browser_state_restores_session(tmp_path: Path, form_server: str):
    """save_browser_state() + load_browser_state() round-trip should work."""
    from gptme.tools.browser import (
        get_current_url,
        load_browser_state,
        open_page,
        save_browser_state,
    )

    # Step 1: open a page and save the session
    open_page(f"{form_server}/")
    state_path = str(tmp_path / "session.json")
    save_browser_state(state_path)

    # Step 2: reload the state in the same session
    result = load_browser_state(state_path)
    assert result, "load_browser_state() returned empty string"
    assert "loaded" in result.lower() or "state" in result.lower(), (
        f"load_browser_state() should confirm state was loaded, got: {result!r}"
    )

    # Step 3: open the same page again — should work with restored state
    snapshot = open_page(f"{form_server}/")
    assert snapshot, "open_page() after load_browser_state() returned empty snapshot"

    url = get_current_url()
    assert "127.0.0.1" in url or "localhost" in url, (
        f"After reload, URL should be form server URL, got: {url!r}"
    )


@pytest.mark.integration
def test_load_browser_state_raises_on_missing_file():
    """load_browser_state() should raise FileNotFoundError for a nonexistent file."""
    from gptme.tools.browser import load_browser_state

    with pytest.raises(FileNotFoundError):
        load_browser_state("/nonexistent/path/state.json")
