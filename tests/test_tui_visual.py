"""
Visual regression tests for the gptme TUI using SVG snapshots.

Captures the TUI at a fixed terminal size and compares against stored baselines.
A mismatch means the visual output changed — either a regression or an intentional
change that needs a new baseline.

To regenerate baselines after an intentional visual change:
    pytest tests/test_tui_visual.py --snapshot-update

On failure, the actual SVG is written next to the baseline for manual inspection.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("textual")

from gptme.logmanager import LogManager
from gptme.tui.app import GptmeApp

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
# Fixed terminal size for stable, reproducible renders
TERM_SIZE = (80, 24)


def make_manager(tmp_path):
    return LogManager([], logdir=tmp_path / "conv", lock=False)


def _normalize_svg(svg: str) -> str:
    """Replace the run-specific hash in CSS class names for stable comparison."""
    return re.sub(r"terminal-\d{6,}", "terminal-HASH", svg)


def _rect_fills(svg: str) -> list[str]:
    """Extract all explicit fill colors from rect elements."""
    return re.findall(r'<rect[^>]*\bfill="(#[0-9a-fA-F]{3,8})"', svg)


def _is_mid_gray(color: str) -> bool:
    """True if the color is a mid-range neutral gray (the gray background bug range)."""
    h = color.lstrip("#")
    if len(h) != 6:
        return False
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    max_channel_diff = max(abs(r - g), abs(g - b), abs(r - b))
    # Must be nearly neutral (R≈G≈B) and in the mid range that textual-dark uses
    # for $panel / $surface (roughly 0x20–0xB0).  Very dark blacks (#121212 etc.)
    # and near-white colors are intentional and not the regression target.
    return max_channel_diff <= 20 and 0x20 <= r <= 0xB0


def _check_no_gray_rects(svg: str, label: str) -> None:
    """Fail if any mid-gray fill appears in a content-area rect."""
    fills = _rect_fills(svg)
    gray_fills = [c for c in fills if _is_mid_gray(c)]
    # #292929 is the terminal window frame border — intentional, not a content gray
    gray_fills = [c for c in gray_fills if c.lower() != "#292929"]
    assert not gray_fills, (
        f"{label}: mid-gray background color(s) detected in rendered SVG: "
        f"{gray_fills!r}. This is likely a regression of the gray active-output "
        "strip bug (see gptme#3334). Check that native_ansi_color is True."
    )


def _snapshot_check(name: str, svg: str, *, update: bool) -> None:
    path = SNAPSHOT_DIR / f"{name}.svg"
    normalized = _normalize_svg(svg)

    if update:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(normalized, encoding="utf-8")
        return

    if not path.exists():
        pytest.fail(
            f"Snapshot '{name}' not found at {path}. "
            "Run `pytest tests/test_tui_visual.py --snapshot-update` to generate it."
        )

    baseline = path.read_text(encoding="utf-8")
    if normalized != baseline:
        actual_path = path.with_suffix(".actual.svg")
        actual_path.write_text(normalized, encoding="utf-8")
        pytest.fail(
            f"Snapshot '{name}' differs from baseline.\n"
            f"  baseline : {path}\n"
            f"  actual   : {actual_path}\n"
            "If this change is intentional, run `pytest tests/test_tui_visual.py "
            "--snapshot-update` to accept the new baseline."
        )


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def snapshot_update(request):
    return request.config.getoption("--snapshot-update", default=False)


# ── tests ─────────────────────────────────────────────────────────────────────


# These three tests document the correct behavior: no mid-gray ($panel/#262626) should
# appear in the content area.  They currently fail on master because the fix (native_ansi_color=True
# on ActiveOutput) has not landed yet — see gptme#3334.  Mark xfail so CI stays green; remove
# the markers once #3334 (or equivalent) merges.
_GRAY_BUG_XFAIL = pytest.mark.xfail(
    strict=False,
    reason="Content-area gray background still present on master (#262626 from Textual $panel). "
    "Will pass once native_ansi_color fix in #3334 is merged.",
    run=True,
)


@_GRAY_BUG_XFAIL
@pytest.mark.asyncio
async def test_idle_state_no_gray_background(tmp_path):
    """Idle TUI must not render any mid-gray content-area background (regression #3334)."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
    _check_no_gray_rects(svg, "idle state")


@_GRAY_BUG_XFAIL
@pytest.mark.asyncio
async def test_stream_state_no_gray_background(tmp_path):
    """Streaming state must not render a gray active-output strip (regression #3334)."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        app._begin_stream()
        await pilot.pause()
        svg = app.export_screenshot()
    _check_no_gray_rects(svg, "streaming state")


@_GRAY_BUG_XFAIL
@pytest.mark.asyncio
async def test_inline_mode_no_gray_background(tmp_path):
    """Inline mode must not render any mid-gray content-area background."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
    _check_no_gray_rects(svg, "inline mode")


@pytest.mark.asyncio
async def test_idle_state_snapshot(tmp_path, snapshot_update):
    """Full visual snapshot of the idle TUI — catches palette and layout regressions."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
    _snapshot_check("tui_idle", svg, update=snapshot_update)


@pytest.mark.asyncio
async def test_stream_state_snapshot(tmp_path, snapshot_update):
    """Full visual snapshot of the streaming TUI — catches active-output regressions."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        app._begin_stream()
        await pilot.pause()
        svg = app.export_screenshot()
    _snapshot_check("tui_stream", svg, update=snapshot_update)


@pytest.mark.asyncio
async def test_inline_mode_snapshot(tmp_path, snapshot_update):
    """Full visual snapshot of inline mode — catches per-mode rendering regressions."""
    app = GptmeApp(make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test(size=TERM_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
    _snapshot_check("tui_inline", svg, update=snapshot_update)
