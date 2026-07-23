"""
Visual regression tests for the gptme TUI.

Uses Textual's ``App.export_screenshot()`` to produce deterministic SVG captures
of key TUI states and compares them against committed baselines.

**To regenerate baselines** (after an intentional visual change)::

    pytest tests/test_tui_visual.py --snapshot-update

On failure, actual and baseline SVGs are written to ``tests/snapshots/tui/actual/``
for side-by-side inspection. CI uploads that directory as a build artifact so
reviewers can diff the renders without a local checkout.

**Acceptance criteria covered**:

* ``test_streaming_placeholder_background`` — a return of the gray active-output
  strip (#3334) changes the snapshot and fails this test.
* ``test_message_palette`` — a global swap of the foreground/accent palette
  changes the snapshot and fails this test.
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("textual")

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tui.app import GptmeApp

# ---------------------------------------------------------------------------
# Force a fixed, offline model so the TUI status bar renders identically on
# every host (CI has no API keys; local envs may have different providers).
# This overrides the autouse `init_` fixture from conftest.py.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def init_(monkeypatch):
    monkeypatch.setenv("MODEL", "local/test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:666")
    from gptme.init import init

    init("local/test", interactive=False, tool_allowlist=None, tool_format="markdown")


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "tui"

# Fixed terminal size keeps renders deterministic across hosts.
TERMINAL_SIZE = (100, 30)


def _make_manager(tmp_path: Path, msgs: list | None = None) -> LogManager:
    return LogManager(msgs or [], logdir=tmp_path / "conv", lock=False)


def _normalize_svg(svg: str) -> str:
    """Strip elements that vary across Rich/Textual patch releases but carry no
    visual meaning, so minor library updates don't force baseline regeneration.

    Keeps all color values and layout coordinates intact — those ARE the visual
    signal we want to protect.
    """
    # Rich embeds its own numeric hash in every class name it emits.  The hash
    # is derived from the color table; it is stable across runs with the same
    # palette but changes when Rich's CSS-generation code changes even if no
    # color moved.  Replace it with a fixed placeholder so palette-identical
    # renders compare equal regardless of Rich version.
    svg = re.sub(r"terminal-\d+", "terminal-HASH", svg)
    # The TUI status bar (bottom line) shows dynamic state: model name, token
    # counts, and the current state label.  Token counts vary between test runs
    # depending on how many tools were initialised before the test.  Replace
    # the status-bar text element — including its variable textLength attribute
    # and text content — with fixed placeholders so tests that run after other
    # test files see the same SVG.
    # Pattern: <text … textLength="NNN.N" …>…model… | state</text>
    svg = re.sub(
        r'(<text\b[^>]*?\b)textLength="[\d.]+"([^>]*>)[^<]*(idle|generating|streaming|interrupt)[^<]*(</text>)',
        r'\1textLength="0"\2STATUS_BAR\4',
        svg,
    )
    # Strip trailing whitespace per line (pre-commit hooks enforce this on the
    # committed baselines, so we must match that format when comparing).
    lines = [line.rstrip() for line in svg.splitlines()]
    return "\n".join(lines).strip()


def _assert_svg_matches_snapshot(
    svg: str,
    name: str,
    *,
    update: bool,
) -> None:
    """Compare *svg* against the stored baseline for *name*.

    * ``update=True``: write the normalised SVG as the new baseline and return.
    * ``update=False``: fail with a diff summary and artifact paths when the
      SVG does not match; pass silently when it does.
    """
    normalized = _normalize_svg(svg)
    baseline_path = SNAPSHOT_DIR / f"{name}.svg"
    artifacts_dir = SNAPSHOT_DIR / "actual"

    if update or not baseline_path.exists():
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(normalized, encoding="utf-8")
        return

    stored = baseline_path.read_text(encoding="utf-8")
    if normalized == stored:
        return

    # Persist artifacts so CI can upload them.
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    actual_file = artifacts_dir / f"{name}.svg"
    baseline_copy = artifacts_dir / f"{name}.baseline.svg"
    actual_file.write_text(normalized, encoding="utf-8")
    baseline_copy.write_text(stored, encoding="utf-8")

    # Build a human-readable first-diff summary (max 8 lines shown).
    actual_lines = normalized.splitlines()
    stored_lines = stored.splitlines()
    diffs = [
        f"  L{i + 1}: {stored_lines[i]!r}\n        → {actual_lines[i]!r}"
        for i in range(min(len(actual_lines), len(stored_lines)))
        if actual_lines[i] != stored_lines[i]
    ][:8]
    length_note = (
        f"\n  (line counts differ: baseline={len(stored_lines)}, "
        f"actual={len(actual_lines)})"
        if len(actual_lines) != len(stored_lines)
        else ""
    )

    pytest.fail(
        f"SVG snapshot {name!r} does not match baseline.\n"
        + "\n".join(diffs)
        + length_note
        + f"\n\nActual:   {actual_file}"
        + f"\nBaseline: {baseline_path}"
        + "\n\nRe-run with --snapshot-update to accept the new render."
    )


@pytest.fixture()
def snapshot_update(request) -> bool:
    return bool(request.config.getoption("--snapshot-update", default=False))


# ---------------------------------------------------------------------------
# Visual regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tui_idle_state(tmp_path, snapshot_update):
    """Idle TUI (no messages, waiting for input) matches its baseline.

    Catches palette or layout changes that affect the default empty screen.
    """
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()

    _assert_svg_matches_snapshot(svg, "idle", update=snapshot_update)


@pytest.mark.asyncio
async def test_streaming_placeholder_background(tmp_path, snapshot_update):
    """Streaming placeholder uses the same background as the chat surface.

    Regression guard for gptme#3334: the active-output surface briefly used
    Textual's gray themed background instead of the terminal-native background,
    producing a visible gray strip during generation.  A return of that gray
    background changes this snapshot.
    """
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        await pilot.pause()
        app._begin_stream()
        await pilot.pause()
        svg = app.export_screenshot()

    _assert_svg_matches_snapshot(svg, "streaming", update=snapshot_update)


@pytest.mark.asyncio
async def test_message_palette(tmp_path, snapshot_update):
    """Conversation with user + assistant messages matches its baseline.

    Catches global palette swaps: user messages have a green left border
    (``$success``), assistant messages have a blue left border (``$primary``).
    Any swap of these accent colours changes this snapshot.
    """
    manager = _make_manager(
        tmp_path,
        [
            Message("user", "hello there"),
            Message("assistant", "Hi! How can I help?"),
            Message("system", "```stdout\ntool output\n```"),
        ],
    )
    app = GptmeApp(manager, workspace=tmp_path)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()

    _assert_svg_matches_snapshot(svg, "message_palette", update=snapshot_update)


@pytest.mark.asyncio
async def test_inline_idle_state(tmp_path, snapshot_update):
    """Idle ``--inline`` TUI matches its baseline.

    ``--inline`` renders without an alternate screen; a separate baseline
    catches layout or background changes specific to that mode.
    """
    app = GptmeApp(_make_manager(tmp_path), workspace=tmp_path, inline=True)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()

    _assert_svg_matches_snapshot(svg, "inline_idle", update=snapshot_update)
