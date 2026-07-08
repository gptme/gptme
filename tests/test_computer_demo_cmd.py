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
        assert data["total_ms"] == 0
        assert data["steps"] == []

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

    def test_fill_mismatch_aborts_before_submit(self):
        """If typed text does not round-trip, demo stops before submit/verify."""
        page = _make_page_mock()

        compose_el = MagicMock()
        compose_el.inner_text.return_value = ""
        compose_el.click.return_value = None
        compose_el.fill.return_value = None

        btn_el = MagicMock()
        status_el = MagicMock()
        status_el.inner_text.return_value = "tweet-posted:"

        def _locator(sel):
            if "tweetTextarea_0" in sel:
                return compose_el
            if "tweetButtonInline" in sel:
                return btn_el
            if sel == "#status":
                return status_el
            return MagicMock()

        page.locator.side_effect = _locator

        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert [s["step"] for s in data["steps"]] == [
            "launch browser",
            "open_page (load fixture)",
            'wait_for_element [data-testid="tweetTextarea_0"]',
            "fill_element (type tweet)",
        ]
        assert not data["steps"][-1]["ok"]
        btn_el.click.assert_not_called()
        status_el.inner_text.assert_not_called()

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


# ---------------------------------------------------------------------------
# Factorio milestone mock helpers
# ---------------------------------------------------------------------------


def _make_factorio_page_mock() -> MagicMock:
    """Return a page mock that simulates the Factorio milestone fixture."""
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_selector.return_value = None

    status_el = MagicMock()
    status_el.inner_text.return_value = (
        "factorio-milestone:automation-started iron_ore:1 iron_plate:1"
    )

    ore_el = MagicMock()
    ore_el.click.return_value = None

    craft_el = MagicMock()
    craft_el.click.return_value = None

    def _locator(sel):
        if "iron-ore" in sel:
            return ore_el
        if "craft-iron-plate" in sel:
            return craft_el
        if sel == "#status":
            return status_el
        return MagicMock()

    page.locator.side_effect = _locator
    return page


# ---------------------------------------------------------------------------
# Doom milestone mock helpers
# ---------------------------------------------------------------------------


def _make_doom_page_mock(
    initial_status: str = "doom-milestone:waiting score:0 player-at:3 enemy-at:6 enemy-alive:true",
    final_status: str = "doom-milestone:enemy-defeated score:100 player-at:3 enemy-at:6 enemy-alive:false",
) -> MagicMock:
    """Return a page mock that simulates the Doom milestone fixture."""
    page = MagicMock()
    page.goto.return_value = None
    page.keyboard = MagicMock()
    page.keyboard.press.return_value = None

    call_count = [0]
    status_el = MagicMock()

    def _inner_text(timeout=3000):
        call_count[0] += 1
        return initial_status if call_count[0] == 1 else final_status

    status_el.inner_text.side_effect = _inner_text

    def _locator(sel):
        if sel == "#status":
            return status_el
        return MagicMock()

    page.locator.side_effect = _locator
    return page


# ---------------------------------------------------------------------------
# Factorio milestone tests
# ---------------------------------------------------------------------------


class TestFactorioMilestone:
    """Tests for `gptme-util computer demo --milestone factorio`."""

    def test_milestone_option_in_help(self):
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--milestone" in result.output
        assert "factorio" in result.output

    def test_factorio_success_human_readable(self):
        page = _make_factorio_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "factorio"])
        assert result.exit_code == 0, result.output
        assert "Demo passed" in result.output

    def test_factorio_success_json(self):
        page = _make_factorio_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "factorio", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "pass"
        assert data["milestone"] == "factorio"
        assert all(s["ok"] for s in data["steps"])

    def test_factorio_ore_click_failure_exits_1(self):
        page = _make_factorio_page_mock()
        page.locator.side_effect = lambda sel: (
            _raise_on_call(MagicMock(), "ore click error")
            if "iron-ore-1" in sel
            else MagicMock()
        )
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "factorio", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"

    def test_factorio_craft_failure_exits_1(self):
        """If the craft button click raises, demo exits 1."""
        page = _make_factorio_page_mock()

        craft_fail = MagicMock()
        craft_fail.click.side_effect = Exception("craft failed")

        ore_ok = MagicMock()
        ore_ok.click.return_value = None

        def _locator(sel):
            if "craft-iron-plate" in sel and ":not" not in sel:
                return craft_fail
            if "iron-ore" in sel:
                return ore_ok
            return MagicMock()

        page.locator.side_effect = _locator
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "factorio", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"

    def test_factorio_bad_status_exits_1(self):
        """If status text does not contain the milestone marker, demo exits 1."""
        page = _make_factorio_page_mock()
        status_el = MagicMock()
        status_el.inner_text.return_value = "factorio-milestone:waiting iron_ore:0"
        page.locator.side_effect = lambda sel: (
            status_el if sel == "#status" else MagicMock()
        )
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "factorio", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"


# ---------------------------------------------------------------------------
# Doom milestone tests
# ---------------------------------------------------------------------------


class TestDoomMilestone:
    """Tests for `gptme-util computer demo --milestone doom`."""

    def test_doom_success_human_readable(self):
        page = _make_doom_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "doom"])
        assert result.exit_code == 0, result.output
        assert "Demo passed" in result.output

    def test_doom_success_json(self):
        page = _make_doom_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "doom", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "pass"
        assert data["milestone"] == "doom"
        assert all(s["ok"] for s in data["steps"])

    def test_doom_space_key_sent(self):
        """Space key is dispatched exactly once during the demo."""
        page = _make_doom_page_mock()
        with _make_playwright_patcher(page):
            runner = CliRunner()
            runner.invoke(demo_cmd, ["--milestone", "doom"])
        page.keyboard.press.assert_called_once_with("Space")

    def test_doom_enemy_alive_after_shoot_exits_1(self):
        """If the enemy is still alive after pressing Space, demo exits 1."""
        page = _make_doom_page_mock(
            final_status="doom-milestone:waiting score:0 player-at:3 enemy-at:6 enemy-alive:true",
        )
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "doom", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"

    def test_doom_initial_state_wrong_exits_1(self):
        """If the initial state is not 'waiting', demo exits 1 immediately."""
        page = _make_doom_page_mock(
            initial_status="doom-milestone:enemy-defeated score:100 player-at:3 enemy-at:6 enemy-alive:false",
        )
        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--milestone", "doom", "--json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"


# ---------------------------------------------------------------------------
# --all flag tests
# ---------------------------------------------------------------------------


def _raise_on_call(mock: MagicMock, msg: str) -> MagicMock:
    """Make mock.click() raise an Exception."""
    mock.click.side_effect = Exception(msg)
    return mock


class TestDemoAllFlag:
    """Tests for `gptme-util computer demo --all`."""

    def _make_all_milestones_page(self) -> MagicMock:
        """Return a page mock that satisfies tweet + factorio + doom interactions."""
        tweet_page = _make_page_mock()
        return tweet_page  # reused; each milestone opens its own browser

    def test_all_flag_in_help(self):
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--help"])
        assert "--all" in result.output

    def test_all_passes_when_all_milestones_pass(self):
        """With all three mocks succeeding, --all exits 0."""
        # Each milestone opens its own Playwright context inside _run_milestone_demo.
        # We mock all three page types through the shared sync_playwright patcher.

        tweet_page = _make_page_mock()
        factorio_page = _make_factorio_page_mock()
        doom_page = _make_doom_page_mock()

        pages_by_call = [tweet_page, factorio_page, doom_page]
        call_idx = [0]

        browser = MagicMock()
        context = MagicMock()
        chromium = MagicMock()
        pw_instance = MagicMock()
        pw_instance.chromium = chromium

        def _new_page():
            idx = call_idx[0]
            call_idx[0] += 1
            if idx < len(pages_by_call):
                return pages_by_call[idx]
            return pages_by_call[-1]

        context.new_page.side_effect = _new_page
        browser.new_context.return_value = context
        chromium.launch.return_value = browser

        @contextmanager
        def fake_sync_playwright():
            yield pw_instance

        with patch("gptme.cli.cmd_computer.sync_playwright", fake_sync_playwright):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--all", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "pass"
        assert len(data["milestones"]) == 3
        assert all(m["status"] == "pass" for m in data["milestones"])

    def test_all_fails_when_one_milestone_fails(self):
        """--all exits 1 when any milestone fails."""
        tweet_page = _make_page_mock()
        tweet_page.goto.side_effect = Exception("connection refused")

        factorio_page = _make_factorio_page_mock()
        doom_page = _make_doom_page_mock()

        pages_by_call = [tweet_page, factorio_page, doom_page]
        call_idx = [0]

        browser = MagicMock()
        context = MagicMock()
        chromium = MagicMock()
        pw_instance = MagicMock()
        pw_instance.chromium = chromium

        def _new_page():
            idx = call_idx[0]
            call_idx[0] += 1
            return pages_by_call[idx] if idx < len(pages_by_call) else pages_by_call[-1]

        context.new_page.side_effect = _new_page
        browser.new_context.return_value = context
        chromium.launch.return_value = browser

        @contextmanager
        def fake_sync_playwright():
            yield pw_instance

        with patch("gptme.cli.cmd_computer.sync_playwright", fake_sync_playwright):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--all", "--json"])

        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["status"] == "fail"
        assert any(m["status"] == "fail" for m in data["milestones"])

    def test_all_json_has_milestones_key(self):
        """--all --json output has top-level 'milestones' list."""
        tweet_page = _make_page_mock()
        factorio_page = _make_factorio_page_mock()
        doom_page = _make_doom_page_mock()

        pages_by_call = [tweet_page, factorio_page, doom_page]
        call_idx = [0]

        browser = MagicMock()
        context = MagicMock()
        chromium = MagicMock()
        pw_instance = MagicMock()
        pw_instance.chromium = chromium

        def _new_page():
            idx = call_idx[0]
            call_idx[0] += 1
            return pages_by_call[idx] if idx < len(pages_by_call) else pages_by_call[-1]

        context.new_page.side_effect = _new_page
        browser.new_context.return_value = context
        chromium.launch.return_value = browser

        @contextmanager
        def fake_sync_playwright():
            yield pw_instance

        with patch("gptme.cli.cmd_computer.sync_playwright", fake_sync_playwright):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--all", "--json"])

        data = json.loads(result.output)
        assert "milestones" in data
        assert len(data["milestones"]) == 3
        for ms in data["milestones"]:
            assert "milestone" in ms
            assert "status" in ms
            assert "steps" in ms

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


# ---------------------------------------------------------------------------
# --all / --milestone mutual exclusivity
# ---------------------------------------------------------------------------


class TestDemoMutualExclusivity:
    """--all and --milestone are mutually exclusive."""

    def test_all_and_milestone_raises_usage_error(self):
        """Combining --all with --milestone should exit with a usage error."""
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--all", "--milestone", "factorio"])
        assert result.exit_code != 0
        assert (
            "mutually exclusive" in result.output.lower()
            or "mutually exclusive"
            in (result.exception and str(result.exception) or "")
        )

    def test_milestone_alone_is_valid(self):
        """--milestone without --all should not error on the flag check itself."""
        # We just verify the mutual-exclusivity guard is not triggered.
        # (Playwright is not mocked here, so the demo will fail, but NOT due to the flag check.)
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--milestone", "tweet"])
        # Exit code may be non-zero (playwright absent) but NOT from UsageError
        assert "mutually exclusive" not in result.output

    def test_all_alone_is_valid(self):
        """--all without --milestone should not error on the flag check."""
        runner = CliRunner()
        result = runner.invoke(demo_cmd, ["--all"])
        assert "mutually exclusive" not in result.output


# ---------------------------------------------------------------------------
# Exception handling in _run_milestone_demo
# ---------------------------------------------------------------------------


class TestDemoExceptionHandling:
    """Unexpected exceptions inside _run_milestone_demo return a clean fail dict."""

    def test_unexpected_exception_returns_fail_not_traceback(self):
        """An unexpected error during the demo returns status='fail', not a raw traceback."""
        page = _make_page_mock()
        page.goto.side_effect = RuntimeError("unexpected internal error")

        with _make_playwright_patcher(page):
            runner = CliRunner()
            result = runner.invoke(demo_cmd, ["--json"])

        # Should not propagate as an unhandled exception
        assert result.exit_code == 1
        # Output must be valid JSON (not a raw traceback)
        data = json.loads(result.output)
        assert data["status"] == "fail"
