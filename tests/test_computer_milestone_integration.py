"""Integration tests for the "Can it Doom?" and "Can it Factorio?" milestone pipelines.

Validates the keyboard-driven and click-driven game-control flows for the
remaining two issue #216 milestones, using the same self-contained HTML
fixtures from ``gptme.eval.suites.computer``.

Pipeline under test:

  Doom:      open_page → read_page_text → press_key(Space) → read_page_text
  Factorio:  open_page → click_element(ore×3) → wait_for_element → click_element(craft) → read_page_text

These tests prove the tool pipeline works at the Playwright layer without
requiring a live LLM session.  The same calls work against real games once
the browser session is authenticated / the game window is open.

Run manually (requires Playwright chromium):
    pytest tests/test_computer_milestone_integration.py -v

Marked ``integration`` and skipped when Playwright is absent so they never
block CI in environments without a browser.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Playwright / chromium availability guard
# ---------------------------------------------------------------------------


def _playwright_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _chromium_ok() -> bool:
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
    """Skip integration tests when Playwright chromium is not installed."""
    if not _playwright_available():
        pytest.skip("playwright not installed")
    if not _chromium_ok():
        pytest.skip(
            "Playwright chromium not installed (run: playwright install chromium)"
        )


# ---------------------------------------------------------------------------
# Browser cleanup between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _close_browser_between_tests():
    yield
    try:
        from gptme.tools._browser_playwright import close_page as _close

        _close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# "Can it play Doom?" milestone integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_doom_milestone_initial_state():
    """open_page on the Doom fixture returns the initial waiting state."""
    from gptme.eval.suites.computer import _DOOM_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import open_page

    snapshot = open_page(_DOOM_MILESTONE_FIXTURE_URL)
    assert snapshot, "open_page returned empty string"
    assert "doom-milestone:waiting" in snapshot or "Doom Milestone" in snapshot, (
        f"Expected initial game state, got:\n{snapshot[:400]}"
    )


@pytest.mark.integration
def test_doom_milestone_press_space_defeats_enemy():
    """press_key('Space') on the Doom fixture defeats the enemy in one shot.

    This validates the full 'Can it play Doom?' pipeline:
      open_page → read_page_text → press_key(Space) → read_page_text
    confirms 'doom-milestone:enemy-defeated'.

    The fixture auto-aims the bullet toward the enemy, so a single Space
    from the default player position (cell 3) hits the enemy (cell 6).
    """
    from gptme.eval.suites.computer import _DOOM_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import open_page, press_key, read_page_text

    # Step 1: load the game
    open_page(_DOOM_MILESTONE_FIXTURE_URL)

    # Step 2: verify initial state
    initial = read_page_text()
    assert "doom-milestone:waiting" in initial, (
        f"Expected 'doom-milestone:waiting' in initial state, got:\n{initial[:400]}"
    )
    assert "enemy-alive:true" in initial, (
        f"Expected 'enemy-alive:true' in initial state, got:\n{initial[:400]}"
    )

    # Step 3: fire at the enemy — auto-aims so one Space wins
    press_key("Space")

    # Step 4: verify the milestone marker
    result = read_page_text()
    assert "doom-milestone:enemy-defeated" in result, (
        f"Expected 'doom-milestone:enemy-defeated' after pressing Space, got:\n{result[:400]}"
    )
    assert "score:100" in result, (
        f"Expected score:100 after defeating enemy, got:\n{result[:400]}"
    )
    assert "enemy-alive:false" in result, (
        f"Expected 'enemy-alive:false' after defeat, got:\n{result[:400]}"
    )


@pytest.mark.integration
def test_doom_milestone_arrow_keys_move_player():
    """ArrowRight and ArrowLeft move the player position in the Doom fixture."""
    from gptme.eval.suites.computer import _DOOM_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import open_page, press_key, read_page_text

    open_page(_DOOM_MILESTONE_FIXTURE_URL)

    # Initial player at cell 3
    initial = read_page_text()
    assert "player-at:3" in initial, (
        f"Expected 'player-at:3' initially, got:\n{initial[:400]}"
    )

    # Move right twice — player should be at cell 5
    press_key("ArrowRight")
    press_key("ArrowRight")
    after_move = read_page_text()
    assert "player-at:5" in after_move, (
        f"Expected 'player-at:5' after two ArrowRight presses, got:\n{after_move[:400]}"
    )


# ---------------------------------------------------------------------------
# "Can it play Factorio?" milestone integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_factorio_milestone_initial_state():
    """open_page on the Factorio fixture returns the initial waiting state."""
    from gptme.eval.suites.computer import _FACTORIO_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import open_page

    snapshot = open_page(_FACTORIO_MILESTONE_FIXTURE_URL)
    assert snapshot, "open_page returned empty string"
    assert (
        "factorio-milestone:waiting" in snapshot or "Factorio Milestone" in snapshot
    ), f"Expected initial game state, got:\n{snapshot[:400]}"


@pytest.mark.integration
def test_factorio_milestone_full_pipeline():
    """Full 'Can it play Factorio?' pipeline: gather ore → craft iron plate.

    Validates:
      open_page → click ore nodes (3×) → wait_for_element(craft button enabled)
                → click craft button → read_page_text confirms automation-started
    """
    from gptme.eval.suites.computer import _FACTORIO_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import (
        click_element,
        open_page,
        read_page_text,
        wait_for_element,
    )

    # Step 1: load the game
    open_page(_FACTORIO_MILESTONE_FIXTURE_URL)

    # Step 2: verify initial state
    initial = read_page_text()
    assert "factorio-milestone:waiting" in initial, (
        f"Expected 'factorio-milestone:waiting' initially, got:\n{initial[:400]}"
    )

    # Step 3: click three ore nodes to gather 6 iron ore
    click_element('[data-testid="iron-ore-1"]')
    click_element('[data-testid="iron-ore-2"]')
    click_element('[data-testid="iron-ore-3"]')

    # Step 4: wait for the craft button to become enabled (needs >= 5 ore)
    wait_for_element('[data-testid="craft-iron-plate"]:not([disabled])')

    # Step 5: craft an iron plate
    click_element('[data-testid="craft-iron-plate"]')

    # Step 6: verify the milestone marker
    result = read_page_text()
    assert "factorio-milestone:automation-started" in result, (
        f"Expected 'factorio-milestone:automation-started' after crafting, got:\n{result[:400]}"
    )
    assert "iron_plate:1" in result, (
        f"Expected 'iron_plate:1' after crafting, got:\n{result[:400]}"
    )


@pytest.mark.integration
def test_factorio_milestone_ore_gathering_updates_inventory():
    """Clicking an ore node updates the inventory count in the Factorio fixture."""
    from gptme.eval.suites.computer import _FACTORIO_MILESTONE_FIXTURE_URL
    from gptme.tools.browser import click_element, open_page, read_page_text

    open_page(_FACTORIO_MILESTONE_FIXTURE_URL)

    # Initial inventory: 0 iron ore
    initial = read_page_text()
    assert "iron_ore:0" in initial, (
        f"Expected 'iron_ore:0' initially, got:\n{initial[:400]}"
    )

    # After clicking one ore node: 2 iron ore
    click_element('[data-testid="iron-ore-1"]')
    after_one = read_page_text()
    assert "iron_ore:2" in after_one, (
        f"Expected 'iron_ore:2' after first click, got:\n{after_one[:400]}"
    )

    # After clicking second node: 4 iron ore
    click_element('[data-testid="iron-ore-2"]')
    after_two = read_page_text()
    assert "iron_ore:4" in after_two, (
        f"Expected 'iron_ore:4' after second click, got:\n{after_two[:400]}"
    )
