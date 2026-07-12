"""
Unit tests for computer tool action dispatch (window_focus, wait_for_change)
and the observe_web / observe_desktop helper functions.

All tests use a mock transport — no X11 display or xdotool required.
"""

from __future__ import annotations

import importlib.util
import struct
import zlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

_PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None

from gptme.tools.computer import (
    _dispatch_transport,
    _poll_for_change,
    act_and_observe,
    fill_native,
    observe_desktop,
    observe_web,
)
from gptme.tools.computer_transport import ComputerTransport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_png(path: Path, color: tuple[int, int, int] = (255, 255, 255)) -> None:
    """Write a minimal 1×1 PNG to *path* using only stdlib (no PIL dependency)."""
    r, g, b = color
    # IHDR chunk: width=1, height=1, bit_depth=8, color_type=2 (RGB), rest=0
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)

    # IDAT chunk: filter byte 0x00 + RGB pixel
    raw = b"\x00" + bytes([r, g, b])
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc)
    )

    # IEND chunk
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend)


class _FixedScreenTransport(ComputerTransport):
    """Transport that always returns screenshots of a fixed colour."""

    def __init__(
        self, tmp_path: Path, color: tuple[int, int, int] = (255, 255, 255)
    ) -> None:
        self._tmp = tmp_path
        self._color = color
        self._call_count = 0
        self.window_focus_calls: list[str] = []

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        self._call_count += 1
        path = self._tmp / f"screen_{self._call_count}.png"
        _write_png(path, self._color)
        return path

    def window_focus(self, pattern: str) -> None:
        self.window_focus_calls.append(pattern)

    # --- required ABC stubs ---
    def close(self) -> None:
        pass

    def key(self, text: str) -> None:
        pass

    def type_text(self, text: str) -> None:
        pass

    def mouse_move(self, x: int, y: int) -> None:
        pass

    def left_click(self) -> None:
        pass

    def right_click(self) -> None:
        pass

    def middle_click(self) -> None:
        pass

    def double_click(self) -> None:
        pass

    def left_click_drag(self, x: int, y: int) -> None:
        pass

    def scroll(self, x: int, y: int, direction: str, amount: int = 3) -> None:
        pass

    def cursor_position(self) -> tuple[int, int]:
        return (0, 0)


class _ChangingScreenTransport(_FixedScreenTransport):
    """Transport that switches pixel colour after a given number of screenshot calls."""

    def __init__(
        self,
        tmp_path: Path,
        initial_color: tuple[int, int, int],
        changed_color: tuple[int, int, int],
        change_after: int,
    ) -> None:
        super().__init__(tmp_path, initial_color)
        self._changed_color = changed_color
        self._change_after = change_after

    def screenshot(self, width: int = 0, height: int = 0) -> Path:
        self._call_count += 1
        color = (
            self._changed_color
            if self._call_count > self._change_after
            else self._color
        )
        path = self._tmp / f"screen_{self._call_count}.png"
        _write_png(path, color)
        return path


# ---------------------------------------------------------------------------
# window_focus tests
# ---------------------------------------------------------------------------


class TestWindowFocusAction:
    def test_window_focus_delegates_to_transport(self, tmp_path: Path) -> None:
        transport = _FixedScreenTransport(tmp_path)
        _dispatch_transport(transport, "window_focus", text="Firefox")
        assert transport.window_focus_calls == ["Firefox"]

    def test_window_focus_passes_pattern_verbatim(self, tmp_path: Path) -> None:
        transport = _FixedScreenTransport(tmp_path)
        _dispatch_transport(transport, "window_focus", text="My App — Tab Title")
        assert transport.window_focus_calls == ["My App — Tab Title"]

    def test_window_focus_raises_without_text(self, tmp_path: Path) -> None:
        transport = _FixedScreenTransport(tmp_path)
        with pytest.raises(ValueError, match="text.*window name pattern.*required"):
            _dispatch_transport(transport, "window_focus", text=None)

    def test_window_focus_returns_none(self, tmp_path: Path) -> None:
        transport = _FixedScreenTransport(tmp_path)
        result = _dispatch_transport(transport, "window_focus", text="Terminal")
        assert result is None


# ---------------------------------------------------------------------------
# wait_for_change tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PIL_AVAILABLE, reason="PIL not installed")
class TestWaitForChange:
    def test_returns_screenshot_when_pixels_change(self, tmp_path: Path) -> None:
        """wait_for_change should return the first frame where pixels differ."""
        # Change happens on the 3rd screenshot call (baseline=1st, poll2=same, poll3=different)
        transport = _ChangingScreenTransport(
            tmp_path,
            initial_color=(255, 255, 255),
            changed_color=(0, 0, 0),
            change_after=2,  # calls 1+2 are white; call 3+ are black
        )
        result = _dispatch_transport(transport, "wait_for_change", text="5")
        assert result is not None
        # Should detect the change and return a message
        assert transport._call_count >= 3  # baseline + at least two polls

    def test_returns_final_screenshot_on_timeout(self, tmp_path: Path) -> None:
        """wait_for_change should return a screenshot even when no change occurs."""
        transport = _FixedScreenTransport(tmp_path, color=(128, 128, 128))
        # Tiny timeout so the test finishes quickly
        result = _dispatch_transport(transport, "wait_for_change", text="0.1")
        assert result is not None  # must return something, not None
        assert transport._call_count >= 2  # baseline + at least one poll

    def test_polls_multiple_times(self, tmp_path: Path) -> None:
        """wait_for_change must poll more than once (not bail after first check)."""
        import time

        original_monotonic = time.monotonic
        start = original_monotonic()
        tick = [0]

        # Simulate a 200ms window: first 3 monotonic() calls return times within
        # the deadline, 4th returns past it — guarantees exactly 3 screenshots
        # (baseline + 2 polls) regardless of CI scheduler jitter.
        def stepped_clock() -> float:
            tick[0] += 1
            if tick[0] <= 3:
                return start + tick[0] * 0.05  # 50ms, 100ms, 150ms — within 200ms
            return start + 0.25  # 250ms — past deadline

        transport = _FixedScreenTransport(tmp_path)
        with (
            patch("gptme.tools.computer._monotonic", side_effect=stepped_clock),
            patch("gptme.tools.computer._sleep"),
        ):
            _dispatch_transport(transport, "wait_for_change", text="0.2")
        assert transport._call_count >= 3

    def test_default_timeout_is_ten_seconds(self, tmp_path: Path) -> None:
        """Omitting text should use a 10-second timeout (we just check it doesn't crash)."""
        transport = _FixedScreenTransport(tmp_path)
        # Use an extremely short effective timeout by patching time.monotonic
        import time

        original_monotonic = time.monotonic
        start = original_monotonic()

        call_count = [0]

        def fast_clock() -> float:
            call_count[0] += 1
            # After 5 calls return a value past the 10s deadline
            if call_count[0] > 5:
                return start + 11.0
            return start + call_count[0] * 0.001

        with (
            patch("gptme.tools.computer._monotonic", side_effect=fast_clock),
            patch("gptme.tools.computer._sleep"),
        ):
            result = _dispatch_transport(transport, "wait_for_change")
        assert result is not None


# ---------------------------------------------------------------------------
# observe_web tests
# ---------------------------------------------------------------------------


class TestObserveWeb:
    def test_uses_snapshot_url_when_playwright_available(self) -> None:
        """observe_web() should call snapshot_url() when Playwright is present."""
        fake_snapshot = "# Page: https://example.com\n[link] Example Domain"
        with patch.dict(
            "sys.modules",
            {
                "gptme.tools.browser": MagicMock(
                    has_playwright=lambda: True,
                    snapshot_url=lambda _url: fake_snapshot,
                )
            },
        ):
            msgs = observe_web("https://example.com")

        assert len(msgs) == 1
        assert fake_snapshot in msgs[0].content

    def test_hard_failure_returns_actionable_error(self) -> None:
        """observe_web() surfaces a diagnosis when all observation paths fail.

        Previously returned an empty list, leaving the agent with no feedback.
        Now always returns at least one Message — an error explaining what failed
        and how to fix it — so the agent can self-diagnose instead of looping.
        """
        with (
            patch.dict("sys.modules", {"gptme.tools.browser": None}),
            patch("gptme.tools.computer.computer", return_value=None),
        ):
            result = observe_web("https://example.com")
        assert isinstance(result, list)
        assert len(result) == 1, "hard failure must yield exactly one error message"
        error_text = result[0].content
        assert "failed" in error_text.lower(), "error message must say what failed"
        assert "playwright" in error_text.lower(), "error must mention Playwright"

    def test_missing_playwright_branch_returns_single_actionable_error(self) -> None:
        """observe_web() reports missing Playwright once when browser imports work."""
        browser_module = MagicMock(
            has_playwright=lambda: False,
            snapshot_url=MagicMock(),
            screenshot_url=MagicMock(),
        )
        with (
            patch.dict("sys.modules", {"gptme.tools.browser": browser_module}),
            patch("gptme.tools.computer.computer", return_value=None),
        ):
            result = observe_web("https://example.com")

        assert len(result) == 1
        error_text = result[0].content
        assert error_text.count("Playwright not installed") == 1
        assert "snapshot_url unavailable" in error_text
        browser_module.snapshot_url.assert_not_called()
        browser_module.screenshot_url.assert_not_called()

    def test_screenshot_too_appends_second_message(self) -> None:
        """screenshot_too=True should add a browser screenshot alongside the snapshot."""
        fake_snapshot = "# Page snapshot"
        fake_screenshot_path = "/tmp/fake_screenshot.png"

        with (
            patch.dict(
                "sys.modules",
                {
                    "gptme.tools.browser": MagicMock(
                        has_playwright=lambda: True,
                        snapshot_url=lambda _url: fake_snapshot,
                        screenshot_url=lambda _url: fake_screenshot_path,
                    )
                },
            ),
            patch(
                "gptme.tools.computer._make_screenshot_msg",
                return_value=MagicMock(content="browser screenshot"),
            ),
        ):
            msgs = observe_web("https://example.com", screenshot_too=True)

        assert len(msgs) == 2
        assert fake_snapshot in msgs[0].content
        assert "browser screenshot" in msgs[1].content

    def test_screenshot_too_degrades_gracefully_on_failure(self) -> None:
        """If screenshot_url raises when screenshot_too=True, the snapshot is preserved."""
        fake_snapshot = "# Page snapshot"

        def raise_on_screenshot(_url: str) -> str:
            raise RuntimeError("Playwright timed out")

        with patch.dict(
            "sys.modules",
            {
                "gptme.tools.browser": MagicMock(
                    has_playwright=lambda: True,
                    snapshot_url=lambda _url: fake_snapshot,
                    screenshot_url=raise_on_screenshot,
                )
            },
        ):
            msgs = observe_web("https://example.com", screenshot_too=True)

        # Snapshot must survive even though screenshot raised
        assert len(msgs) == 1
        assert fake_snapshot in msgs[0].content


# ---------------------------------------------------------------------------
# observe_desktop tests
# ---------------------------------------------------------------------------


class TestObserveDesktop:
    def test_delegates_to_computer_screenshot(self) -> None:
        """observe_desktop() must call computer('screenshot')."""
        fake_msg = MagicMock()
        with patch("gptme.tools.computer.computer", return_value=fake_msg) as mock_c:
            result = observe_desktop()
        mock_c.assert_called_once_with("screenshot")
        assert result is fake_msg

    def test_returns_none_when_screenshot_fails(self) -> None:
        """observe_desktop() propagates None when computer() returns None."""
        with patch("gptme.tools.computer.computer", return_value=None):
            result = observe_desktop()
        assert result is None


# ---------------------------------------------------------------------------
# act_and_observe tests
# ---------------------------------------------------------------------------


class TestActAndObserve:
    """Tests for the act_and_observe() helper."""

    def test_click_triggers_wait_for_change(self) -> None:
        """State-changing action must call computer(action) then computer('wait_for_change')."""
        action_msg = MagicMock()
        settled_msg = MagicMock()
        call_args: list[tuple] = []

        def mock_computer(action, text=None, coordinate=None):
            call_args.append((action, text, coordinate))
            if action == "wait_for_change":
                return settled_msg
            return action_msg if action == "left_click" else None

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            msgs = act_and_observe("left_click", coordinate=(100, 200))

        assert len(call_args) == 2
        assert call_args[0] == ("left_click", None, (100, 200))
        assert call_args[1][0] == "wait_for_change"
        assert settled_msg in msgs

    def test_type_action_triggers_wait_for_change(self) -> None:
        """Typing text must also call wait_for_change after the type action."""
        settled_msg = MagicMock()
        call_args: list[tuple] = []

        def mock_computer(action, text=None, coordinate=None):
            call_args.append((action, text, coordinate))
            return settled_msg if action == "wait_for_change" else None

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            msgs = act_and_observe("type", text="hello")

        assert call_args[0][0] == "type"
        assert call_args[1][0] == "wait_for_change"
        assert settled_msg in msgs

    def test_screenshot_action_is_passthrough(self) -> None:
        """Observation-only actions must not append an extra wait_for_change call."""
        screenshot_msg = MagicMock()
        calls: list[str] = []

        def mock_computer(action, text=None, coordinate=None):
            calls.append(action)
            return screenshot_msg

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            msgs = act_and_observe("screenshot")

        assert calls == ["screenshot"], (
            "only one computer() call for observation-only action"
        )
        assert msgs == [screenshot_msg]

    def test_wait_for_change_is_passthrough(self) -> None:
        """wait_for_change itself must not trigger another wait_for_change call."""
        settled_msg = MagicMock()
        calls: list[str] = []

        def mock_computer(action, text=None, coordinate=None):
            calls.append(action)
            return settled_msg

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            msgs = act_and_observe("wait_for_change", text="5")

        assert calls == ["wait_for_change"]
        assert msgs == [settled_msg]

    def test_timeout_forwarded_to_wait_for_change(self) -> None:
        """The timeout argument must be passed as text= to computer('wait_for_change')."""
        captured_text: list[str | None] = []

        def mock_computer(action, text=None, coordinate=None):
            if action == "wait_for_change":
                captured_text.append(text)
            return

        with (
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch("gptme.tools.computer.computer", side_effect=mock_computer),
        ):
            act_and_observe("left_click", coordinate=(0, 0), timeout=7.5)

        assert captured_text == ["7.5"]

    def test_action_result_included_when_not_none(self) -> None:
        """If computer(action) returns a Message, it must appear before the screenshot."""
        action_msg = MagicMock()
        settled_msg = MagicMock()

        def mock_computer(action, text=None, coordinate=None):
            if action == "wait_for_change":
                return settled_msg
            return action_msg

        with (
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch("gptme.tools.computer.computer", side_effect=mock_computer),
        ):
            msgs = act_and_observe("scroll", coordinate=(0, 0), text="down")

        assert msgs[0] is action_msg
        assert msgs[1] is settled_msg

    def test_no_settled_screenshot_when_wait_returns_none(self) -> None:
        """If wait_for_change returns None, the list must not include it."""

        def mock_computer(action, text=None, coordinate=None):
            return None

        with (
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch("gptme.tools.computer.computer", side_effect=mock_computer),
        ):
            msgs = act_and_observe("left_click", coordinate=(100, 100))

        assert msgs == []

    def test_key_action_triggers_wait_for_change(self) -> None:
        """Keyboard actions must also be treated as state-changing."""
        calls: list[str] = []

        def mock_computer(action, text=None, coordinate=None):
            calls.append(action)
            return

        with (
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch("gptme.tools.computer.computer", side_effect=mock_computer),
        ):
            act_and_observe("key", text="Return")

        assert "wait_for_change" in calls, "key action must trigger wait_for_change"


# ---------------------------------------------------------------------------
# act_and_observe pre-action baseline tests (issue #216 race condition fix)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _PIL_AVAILABLE, reason="PIL not installed")
class TestActAndObservePreBaseline:
    """Verify that act_and_observe captures a baseline BEFORE the action.

    The bug (issue #216): window_focus and similar actions change the screen
    immediately.  The old code took a baseline *after* the action, so
    wait_for_change saw no further change and timed out — causing the
    'delay' symptom when opening terminal windows in Xvfb.

    The fix: _poll_for_change is called with a baseline captured *before*
    the action, so the immediate screen change is detected correctly.
    """

    def test_poll_for_change_detects_immediate_change(self, tmp_path: Path) -> None:
        """_poll_for_change with a pre-action baseline detects a same-call change.

        Simulate the window_focus race: baseline (white) is taken before the
        action; after the action, all subsequent screenshots are black.
        The polling must detect the change even on its very first poll.
        """
        # Baseline = white (pre-action state)
        baseline_path = tmp_path / "baseline.png"
        _write_png(baseline_path, (255, 255, 255))

        # Transport always returns black (post-action state)
        transport = _FixedScreenTransport(tmp_path, color=(0, 0, 0))

        result = _poll_for_change(transport, baseline_path, timeout=1.0)
        assert result is not None, "change should be detected"
        assert transport._call_count >= 1

    def test_act_and_observe_uses_pre_action_baseline_via_transport(
        self, tmp_path: Path
    ) -> None:
        """act_and_observe with a real transport mock passes a pre-action baseline.

        When get_transport() returns a working transport:
        - screenshot() is called BEFORE computer(action) (baseline capture)
        - _poll_for_change is used with that baseline instead of computer('wait_for_change')
        - A change that happens immediately (all poll screenshots differ from baseline)
          is returned in the message list.
        """
        # Transport: first screenshot = white (baseline before action),
        # subsequent = black (after window_focus changes the screen).
        transport = _ChangingScreenTransport(
            tmp_path,
            initial_color=(255, 255, 255),
            changed_color=(0, 0, 0),
            change_after=1,  # call 1 = white baseline, calls 2+ = black
        )

        # computer(action) returns None (window_focus returns no Message)
        with (
            patch("gptme.tools.computer.computer", return_value=None),
            patch("gptme.tools.computer.get_transport", return_value=transport),
        ):
            msgs = act_and_observe("window_focus", text="Terminal", timeout=1.0)

        # Must return at least one message (the post-action screenshot)
        assert len(msgs) >= 1, (
            "act_and_observe should return a screenshot even for window_focus"
        )

    def test_act_and_observe_falls_back_when_no_transport_and_screenshot_fails(
        self,
    ) -> None:
        """When get_transport()=None AND native screenshot() fails, fall back to computer('wait_for_change').

        This is the path taken in environments without a display (CI, unit tests)
        where screenshot() raises because DISPLAY is not set.
        """
        settled_msg = MagicMock()
        calls: list[str] = []

        def mock_computer(action, text=None, coordinate=None):
            calls.append(action)
            if action == "wait_for_change":
                return settled_msg
            return None

        with (
            patch("gptme.tools.computer.computer", side_effect=mock_computer),
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch(
                "gptme.tools.computer.screenshot",
                side_effect=RuntimeError("no display"),
            ),
        ):
            msgs = act_and_observe("left_click", coordinate=(100, 100))

        assert "wait_for_change" in calls, (
            "fallback path must call computer('wait_for_change') when screenshot() fails"
        )
        assert settled_msg in msgs

    def test_act_and_observe_native_baseline_uses_poll_for_change(
        self, tmp_path: Path
    ) -> None:
        """When get_transport()=None but screenshot() succeeds, use _poll_for_change.

        This is the native xdotool path (no GPTME_COMPUTER_TRANSPORT set) with a
        working X11 display.  _poll_for_change should be used with the native
        baseline so settle_time works and immediate changes (e.g. window_focus)
        are detected.
        """
        # A pre-action baseline (white) and a post-action screenshot (black).
        baseline_path = tmp_path / "baseline.png"
        _write_png(baseline_path, (255, 255, 255))
        post_action_path = tmp_path / "post.png"
        _write_png(post_action_path, (0, 0, 0))

        screenshot_calls: list[int] = [0]

        def mock_screenshot() -> Path:
            # First call = pre-action baseline (white); subsequent = changed (black)
            screenshot_calls[0] += 1
            if screenshot_calls[0] == 1:
                return baseline_path
            return post_action_path

        with (
            patch("gptme.tools.computer.computer", return_value=None),
            patch("gptme.tools.computer.get_transport", return_value=None),
            patch("gptme.tools.computer.screenshot", side_effect=mock_screenshot),
            patch("gptme.tools.computer._resize_image"),  # no ImageMagick in CI
            patch(
                "gptme.tools.computer._get_api_resolution", return_value=(1024, 768)
            ),  # no display in CI
        ):
            msgs = act_and_observe("left_click", coordinate=(100, 100), timeout=1.0)

        # The poll must have fired: at least one screenshot after the action
        assert screenshot_calls[0] >= 2, (
            "native baseline path must poll for changes after the action"
        )
        # A settled screenshot should be returned
        assert len(msgs) >= 1, (
            "native baseline path must return at least one message (the settled screenshot)"
        )


# ---------------------------------------------------------------------------
# TestTripleClick
# ---------------------------------------------------------------------------


class TestTripleClick:
    """Tests for the triple_click action via the mock transport."""

    def test_triple_click_dispatches_to_transport(self):
        """triple_click must call transport.triple_click()."""
        transport = MagicMock(spec=ComputerTransport)
        _dispatch_transport(transport, "triple_click", None, None)
        transport.triple_click.assert_called_once()

    def test_triple_click_with_coordinate_moves_then_clicks(self):
        """triple_click with coordinate must move mouse first, then triple_click."""
        transport = MagicMock(spec=ComputerTransport)
        _dispatch_transport(transport, "triple_click", None, (100, 200))
        transport.mouse_move.assert_called_once_with(100, 200)
        transport.triple_click.assert_called_once()

    def test_triple_click_no_coordinate_skips_mouse_move(self):
        """triple_click without coordinate must NOT move the mouse first."""
        transport = MagicMock(spec=ComputerTransport)
        _dispatch_transport(transport, "triple_click", None, None)
        transport.mouse_move.assert_not_called()
        transport.triple_click.assert_called_once()


# ---------------------------------------------------------------------------
# TestFillNative
# ---------------------------------------------------------------------------


class TestFillNative:
    """Tests for fill_native() — click, select-all, type sequence."""

    def test_fill_native_calls_triple_click_then_type(self):
        """fill_native must call triple_click then type in sequence."""
        calls: list[tuple] = []

        def mock_computer(action, text=None, coordinate=None):
            calls.append((action, coordinate, text))
            return

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            fill_native((300, 200), "new text")

        assert len(calls) == 3, f"Expected 3 calls, got {len(calls)}: {calls}"
        assert calls[0] == ("triple_click", (300, 200), None), (
            f"First call should be triple_click with coordinate, got {calls[0]}"
        )
        assert calls[1][0] == "type", f"Second call should be type, got {calls[1][0]}"
        assert calls[1][2] == "new text", (
            f"type call should carry the replacement text, got {calls[1][2]}"
        )
        assert calls[2][0] == "screenshot", (
            f"Third call should be screenshot for post-fill observation, got {calls[2][0]}"
        )

    def test_fill_native_returns_list(self):
        """fill_native always returns a list (empty when computer returns None)."""
        with patch("gptme.tools.computer.computer", return_value=None):
            result = fill_native((100, 100), "hello")
        assert isinstance(result, list)

    def test_fill_native_includes_messages_from_computer(self):
        """fill_native collects non-None messages returned by computer()."""
        msg = MagicMock()

        def mock_computer(action, text=None, coordinate=None):
            if action == "type":
                return msg
            return None

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            result = fill_native((100, 100), "hello")

        assert msg in result, "type result message must be in fill_native return value"

    def test_fill_native_coordinate_is_passed_to_triple_click(self):
        """The coordinate argument must be forwarded to triple_click unchanged."""
        seen: list[tuple] = []

        def mock_computer(action, text=None, coordinate=None):
            seen.append((action, coordinate))
            return

        with patch("gptme.tools.computer.computer", side_effect=mock_computer):
            fill_native((760, 45), "https://example.com")

        assert seen[0] == ("triple_click", (760, 45)), (
            f"triple_click must receive the exact coordinate, got {seen[0]}"
        )
