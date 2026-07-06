"""Tests for `gptme-util computer demo` (cmd_computer.py).

Unit-tests the demo CLI command without requiring a real browser or Playwright
installation.  The playwright context manager is monkey-patched so the tests
run in any environment.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gptme.cli.cmd_computer import demo_cmd

# ---------------------------------------------------------------------------
# Playwright mock helpers
# ---------------------------------------------------------------------------


def _make_page_mock(status_text: str = "tweet-posted:Hello from gptme!") -> MagicMock:
    """Return a page mock that simulates a successful tweet-compose interaction."""
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_selector.return_value = None

    compose_el = MagicMock()
    compose_el.inner_text.return_value = "Hello from gptme!"
    compose_el.click.return_value = None
    compose_el.fill.return_value = None
    page.locator.side_effect = lambda sel: (
        compose_el if "tweetTextarea_0" in sel else MagicMock()
    )

    status_el = MagicMock()
    status_el.inner_text.return_value = status_text

    # override: #status locator should return status_el
    def _locator(sel):
        if "tweetTextarea_0" in sel:
            return compose_el
        if sel == "#status":
            return status_el
        return MagicMock()

    page.locator.side_effect = _locator
    return page


def _make_playwright_patcher(page: MagicMock):
    """Return a context-manager patcher that injects the given page mock."""
    browser = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    browser.new_context.return_value = context

    chromium = MagicMock()
    chromium.launch.return_value = browser

    pw_instance = MagicMock()
    pw_instance.chromium = chromium

    @contextmanager
    def fake_sync_playwright():
        yield pw_instance

    return patch("gptme.cli.cmd_computer.sync_playwright", fake_sync_playwright)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDemoCmd:
    """Tests for `gptme-util computer demo`."""

    def test_help_text_present(self):
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--text" in result.output
        assert "--json" in result.output

    def test_success_human_readable(self):
        """Successful run prints all steps as ✓ and exits 0."""
        page = _make_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, [])
        assert result.exit_code == 0, result.output
        assert "Demo passed" in result.output
        assert "✓" in result.output or "pass" in result.output

    def test_success_json_output(self):
        """--json output has status=pass and all steps ok=true."""
        page = _make_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "pass"
        assert all(s["ok"] for s in data["steps"])
        assert data["total_ms"] >= 0

    def test_custom_text_forwarded(self):
        """--text value is typed into the compose box and echoed back."""
        page = _make_page_mock(status_text="tweet-posted:Shipped it!")

        compose_el = MagicMock()
        compose_el.inner_text.return_value = "Shipped it!"
        compose_el.click.return_value = None
        compose_el.fill.return_value = None

        status_el = MagicMock()
        status_el.inner_text.return_value = "tweet-posted:Shipped it!"

        def _locator(sel):
            if "tweetTextarea_0" in sel:
                return compose_el
            if sel == "#status":
                return status_el
            return MagicMock()

        page.locator.side_effect = _locator

        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--text", "Shipped it!", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "pass"
        fill_step = next(s for s in data["steps"] if "fill_element" in s["step"])
        assert fill_step["ok"]

    def test_playwright_missing_exits_1(self):
        """If playwright is not installed (sync_playwright=None), demo exits 1."""
        with patch("gptme.cli.cmd_computer.sync_playwright", None):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, [])
        assert result.exit_code == 1

    def test_playwright_missing_json_output(self):
        """Missing playwright with --json outputs error JSON and exits 1."""
        with patch("gptme.cli.cmd_computer.sync_playwright", None):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "error"

    def test_goto_failure_exits_1(self):
        """If page.goto raises, demo exits 1 and reports the failure."""
        page = _make_page_mock()
        page.goto.side_effect = Exception("net::ERR_ABORTED")
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        failed = [s for s in data["steps"] if not s["ok"]]
        assert any("open_page" in s["step"] for s in failed)

    def test_click_failure_exits_1(self):
        """If click_element raises, demo exits 1."""
        page = _make_page_mock()

        compose_el = MagicMock()
        compose_el.inner_text.return_value = "Hello from gptme!"
        compose_el.click.return_value = None
        compose_el.fill.return_value = None

        btn_el = MagicMock()
        btn_el.click.side_effect = Exception("element not found")

        def _locator(sel):
            if "tweetTextarea_0" in sel:
                return compose_el
            if "tweetButtonInline" in sel:
                return btn_el
            return MagicMock()

        page.locator.side_effect = _locator

        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert any("click_element" in s["step"] and not s["ok"] for s in data["steps"])

    def test_verify_step_fails_when_status_missing(self):
        """If the #status div returns empty text, verify step fails."""
        page = _make_page_mock(status_text="")

        status_el = MagicMock()
        status_el.inner_text.return_value = ""

        compose_el = MagicMock()
        compose_el.inner_text.return_value = "Hello from gptme!"
        compose_el.click.return_value = None
        compose_el.fill.return_value = None

        def _locator(sel):
            if "tweetTextarea_0" in sel:
                return compose_el
            if sel == "#status":
                return status_el
            return MagicMock()

        page.locator.side_effect = _locator

        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        verify_step = next(s for s in data["steps"] if "read_page_text" in s["step"])
        assert not verify_step["ok"]

    def test_demo_url_uses_correct_selectors(self):
        """The fixture URL must embed the Twitter data-testid selectors."""
        from gptme.cli.cmd_computer import _DEMO_TWEET_HTML

        assert 'data-testid="tweetTextarea_0"' in _DEMO_TWEET_HTML
        assert 'data-testid="tweetButtonInline"' in _DEMO_TWEET_HTML
        assert "tweet-posted:" in _DEMO_TWEET_HTML
