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

import http.server
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# ---------------------------------------------------------------------------
# Playwright availability guard
# ---------------------------------------------------------------------------


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _chromium_available() -> bool:
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


# Evaluated once per session — cheaper than launching chromium for every skip check.
_CHROMIUM_OK: bool | None = None


def _chromium_ok() -> bool:
    global _CHROMIUM_OK
    if _CHROMIUM_OK is None:
        _CHROMIUM_OK = _chromium_available()
    return _CHROMIUM_OK


playwright_required = pytest.mark.skipif(
    not _playwright_available(),
    reason="playwright not installed",
)

chromium_required = pytest.mark.skipif(
    not _playwright_available() or not _chromium_ok(),
    reason="Playwright chromium not installed (run: playwright install chromium)",
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
  <button type="submit" id="submit-btn">Post Tweet</button>
</form>
</body>
</html>
"""

_SUBMIT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Submitted</title></head>
<body>
<h1>Posted!</h1>
<p id="result">message={message} author={author}</p>
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
        html = _SUBMIT_HTML_TEMPLATE.format(
            message=params.get("message", ""),
            author=params.get("author", ""),
        )
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
@chromium_required
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
@chromium_required
def test_fill_element_updates_snapshot(form_server: str):
    """fill_element() should fill a field and return an updated ARIA snapshot."""
    from gptme.tools.browser import fill_element, open_page

    open_page(f"{form_server}/")
    result = fill_element("#message", "Hello, world!")
    # The returned snapshot should reflect the page is still open
    assert result is not None
    assert isinstance(result, str)


@pytest.mark.integration
@chromium_required
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

    # Step 3: click the submit button
    click_element("#submit-btn")

    # Wait briefly for the server to process the POST
    time.sleep(0.3)

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
@chromium_required
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
@chromium_required
def test_snapshot_url_reads_form(form_server: str):
    """snapshot_url() (one-shot, no state) should read the ARIA tree of the form page."""
    from gptme.tools.browser import snapshot_url

    result = snapshot_url(f"{form_server}/")
    assert result, "snapshot_url() returned empty string"
    assert any(
        keyword in result.lower() for keyword in ("input", "button", "form", "textbox")
    ), f"Expected form elements in ARIA snapshot:\n{result[:500]}"


@pytest.mark.integration
@chromium_required
def test_observe_web_returns_structured_result(form_server: str):
    """observe_web() should return ARIA-structured content with no screenshot required."""
    from gptme.tools.computer import observe_web

    msgs = observe_web(f"{form_server}/")
    assert msgs, "observe_web() returned empty list"
    combined = " ".join(
        m.content if isinstance(m.content, str) else str(m.content) for m in msgs
    )
    assert combined.strip(), "observe_web() returned messages with no content"
