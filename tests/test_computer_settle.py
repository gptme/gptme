"""Tests for _poll_for_change settle_time and act_and_observe settle behaviour (issue #216).

The settle_time parameter fixes the "delay when opening new terminal windows"
symptom reported in issue #216: a terminal frame appears first, then the shell
prompt renders ~0.3 s later.  Without settle_time the act_and_observe caller
would receive a screenshot of the blank frame and immediately try to type into
an unready xterm.  With settle_time the poller keeps going until the screen
stops changing for settle_time seconds, so the returned screenshot shows the
actual shell prompt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(tmp_path: Path, color: tuple[int, int, int], name: str) -> Path:
    """Write a solid-color PNG and return its path."""
    from PIL import Image

    p = tmp_path / f"{name}.png"
    Image.new("RGB", (64, 64), color).save(p)
    return p


def _tick_monotonic(step: float = 0.1):
    """Return a fake _monotonic() that advances by *step* on every call.

    This avoids the list-exhaustion problem where a capped list returns a
    constant value, causing infinite loops in the settle polling logic.
    """
    count = {"n": 0}

    def _fake():
        t = count["n"] * step
        count["n"] += 1
        return t

    return _fake


# ---------------------------------------------------------------------------
# _poll_for_change — settle_time=0 (original behaviour)
# ---------------------------------------------------------------------------


class TestPollForChangeNoSettle:
    """settle_time=0.0 preserves the original "return on first change" behaviour."""

    def test_returns_on_first_change(self, tmp_path):
        from gptme.tools.computer import _poll_for_change

        baseline = _make_png(tmp_path, (0, 0, 0), "baseline")
        changed = _make_png(tmp_path, (255, 255, 255), "changed")

        screenshots = iter([baseline, changed])

        transport = mock.MagicMock()
        transport.screenshot.side_effect = lambda: next(screenshots)

        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch(
                "gptme.tools.computer._monotonic", side_effect=_tick_monotonic(0.1)
            ),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg",
                return_value=mock.sentinel.msg,
            ),
        ):
            result = _poll_for_change(transport, baseline, timeout=5.0, settle_time=0.0)

        assert result is mock.sentinel.msg
        # Should have screenshotted exactly twice: first (no-change) and second (changed)
        assert transport.screenshot.call_count == 2

    def test_timeout_without_change_returns_current(self, tmp_path):
        from gptme.tools.computer import _poll_for_change

        static = _make_png(tmp_path, (42, 42, 42), "static")
        transport = mock.MagicMock()
        transport.screenshot.return_value = static

        # tick=0.05, timeout=0.2: loop runs 3 times (at 0.05, 0.10, 0.15 < 0.20)
        # then the while condition at 0.20 fails and we fall through to the timeout path.
        tick = _tick_monotonic(0.05)

        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch("gptme.tools.computer._monotonic", side_effect=tick),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg",
                return_value=mock.sentinel.timeout_msg,
            ),
        ):
            result = _poll_for_change(transport, static, timeout=0.2, settle_time=0.0)

        assert result is mock.sentinel.timeout_msg


# ---------------------------------------------------------------------------
# _poll_for_change — settle_time > 0 (settle phase)
# ---------------------------------------------------------------------------


class TestPollForChangeWithSettle:
    """settle_time > 0 keeps polling until the screen stops changing."""

    def test_multi_phase_transition_returns_last_changed_frame(self, tmp_path):
        """Multi-phase transition: frame1 → frame2 → stable.

        Without settle the poller would return after frame1 (first change from baseline).
        With settle it continues until frame2 stabilises and returns the frame2 screenshot.

        Timeline with 0.1 s ticks and settle_time=0.3:
          tick 0  → deadline = 0+5=5
          tick 1  → loop check (in)
          screenshot 1 → baseline (same, no change)
          tick 2  → loop check (in)
          screenshot 2 → frame1 (CHANGE from baseline!)
                          last_change_at = tick 3 = 0.3
          tick 4  → loop check (in)
          screenshot 3 → frame2 (CHANGE from frame1!)
                          last_change_at = tick 5 = 0.5
          tick 6  → loop check (in)
          screenshot 4 → frame2 (same as comparison_baseline, no change)
                          settle check: tick 7 = 0.7 − 0.5 = 0.2 < 0.3 → keep polling
          tick 8  → loop check (in)
          screenshot 5 → frame2 (no change)
                          settle check: tick 9 = 0.9 − 0.5 = 0.4 ≥ 0.3 → SETTLE! return frame2
        """
        from gptme.tools.computer import _poll_for_change

        baseline = _make_png(tmp_path, (0, 0, 0), "baseline")
        frame1 = _make_png(tmp_path, (100, 100, 100), "frame1")
        frame2 = _make_png(tmp_path, (200, 200, 200), "frame2")

        # 5 screenshots: initial no-change, then two changes, then stable
        screenshots = iter([baseline, frame1, frame2, frame2, frame2])

        transport = mock.MagicMock()
        transport.screenshot.side_effect = lambda: next(screenshots)

        seen_paths: list[Path] = []

        def fake_make_msg(path):
            seen_paths.append(path)
            return mock.MagicMock(name=str(path))

        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch(
                "gptme.tools.computer._monotonic", side_effect=_tick_monotonic(0.1)
            ),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg", side_effect=fake_make_msg
            ),
        ):
            result = _poll_for_change(transport, baseline, timeout=5.0, settle_time=0.3)

        assert result is not None, "_poll_for_change returned None unexpectedly"
        assert seen_paths, "_make_screenshot_msg was never called"
        assert seen_paths[-1] == frame2, (
            f"Expected the settled screenshot to be frame2 ({frame2.name}), "
            f"but got {seen_paths[-1].name!r}. "
            "settle_time should wait for the screen to stop changing, returning "
            "the last changed frame rather than the first-change frame."
        )

    def test_single_phase_returns_after_settle_window(self, tmp_path):
        """Simple case: one change from baseline → changed, then stable.

        Returns the changed frame, but only after settle_time has elapsed
        (not immediately on first detection).
        """
        from gptme.tools.computer import _poll_for_change

        baseline = _make_png(tmp_path, (0, 0, 0), "baseline")
        changed = _make_png(tmp_path, (255, 255, 255), "changed")

        # First poll: no change; subsequent polls: changed (and stays changed)
        screenshots = iter([baseline, changed, changed, changed, changed, changed])

        transport = mock.MagicMock()
        transport.screenshot.side_effect = lambda: next(screenshots)

        seen_paths: list[Path] = []

        def fake_make_msg(path):
            seen_paths.append(path)
            return mock.MagicMock(name=str(path))

        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch(
                "gptme.tools.computer._monotonic", side_effect=_tick_monotonic(0.1)
            ),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg", side_effect=fake_make_msg
            ),
        ):
            result = _poll_for_change(transport, baseline, timeout=5.0, settle_time=0.3)

        assert result is not None
        assert seen_paths
        # The returned frame must be the changed one, not baseline
        assert seen_paths[-1] == changed

    def test_timeout_during_settle_returns_last_changed_frame(self, tmp_path):
        """If timeout fires during the settle window, return the last changed frame."""
        from gptme.tools.computer import _poll_for_change

        baseline = _make_png(tmp_path, (0, 0, 0), "baseline")
        changed = _make_png(tmp_path, (255, 255, 255), "changed")

        # Use a fast tick so timeout fires quickly DURING the settle window.
        # timeout=0.5 with 0.2s/tick: deadline = 0 + 0.5 = 0.5.
        # settle_time=5.0 (very long): won't elapse before timeout.
        screenshots = iter(
            [baseline, changed, changed, changed, changed, changed, changed]
        )

        transport = mock.MagicMock()
        transport.screenshot.side_effect = lambda: next(screenshots)

        seen_paths: list[Path] = []

        def fake_make_msg(path):
            seen_paths.append(path)
            return mock.MagicMock(name=str(path))

        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch(
                "gptme.tools.computer._monotonic", side_effect=_tick_monotonic(0.2)
            ),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg", side_effect=fake_make_msg
            ),
        ):
            result = _poll_for_change(transport, baseline, timeout=0.5, settle_time=5.0)

        # Must return something even on timeout (not None) when a change was seen
        assert result is not None, (
            "Expected a non-None result even when timeout fires during settle window"
        )
        # The returned frame must be the changed one, not the baseline
        assert seen_paths, "_make_screenshot_msg was never called"
        assert seen_paths[-1] == changed, (
            "On timeout during settle phase, should return the last changed frame"
        )

    def test_no_change_before_timeout_returns_current_screenshot(self, tmp_path):
        """If no change is ever detected, return the current screenshot (same as original)."""
        from gptme.tools.computer import _poll_for_change

        static = _make_png(tmp_path, (42, 42, 42), "static")

        transport = mock.MagicMock()
        transport.screenshot.return_value = static

        # Expires quickly: deadline = 0 + 0.1 = 0.1, tick 2 = 0.2 > deadline
        with (
            mock.patch("gptme.tools.computer._sleep"),
            mock.patch(
                "gptme.tools.computer._monotonic", side_effect=_tick_monotonic(0.15)
            ),
            mock.patch(
                "gptme.tools.computer._make_screenshot_msg",
                return_value=mock.sentinel.current,
            ),
        ):
            result = _poll_for_change(transport, static, timeout=0.1, settle_time=0.3)

        assert result is mock.sentinel.current


# ---------------------------------------------------------------------------
# act_and_observe — settle_time parameter
# ---------------------------------------------------------------------------


class TestActAndObserveSettleTime:
    """act_and_observe exposes settle_time and passes it to _poll_for_change."""

    def test_default_settle_time_is_nonzero(self):
        """act_and_observe should default to settle_time=0.2 (not 0) so multi-phase
        UI transitions are handled correctly by default."""
        import inspect

        from gptme.tools.computer import act_and_observe

        sig = inspect.signature(act_and_observe)
        default = sig.parameters["settle_time"].default
        assert default > 0.0, (
            "act_and_observe settle_time should default to > 0 so multi-phase "
            "UI transitions (e.g. xterm frame → shell prompt) are handled "
            "without callers needing to pass settle_time explicitly"
        )

    def test_settle_time_forwarded_to_poll_for_change(self, tmp_path):
        """act_and_observe forwards settle_time to _poll_for_change."""
        from gptme.tools.computer import act_and_observe

        dummy_baseline = _make_png(tmp_path, (0, 0, 0), "dummy")
        mock_transport = mock.MagicMock()
        mock_transport.screenshot.return_value = dummy_baseline

        captured_settle: list[float] = []

        def fake_poll(transport, baseline, timeout, settle_time=0.0):
            captured_settle.append(settle_time)
            return  # no screenshot

        with (
            mock.patch(
                "gptme.tools.computer.get_transport", return_value=mock_transport
            ),
            mock.patch("gptme.tools.computer._poll_for_change", side_effect=fake_poll),
            mock.patch("gptme.tools.computer.computer", return_value=None),
        ):
            act_and_observe("left_click", coordinate=(100, 200), settle_time=0.5)

        assert captured_settle == [0.5], (
            f"Expected settle_time=0.5 forwarded to _poll_for_change, got {captured_settle}"
        )

    def test_settle_time_zero_uses_original_behaviour(self, tmp_path):
        """settle_time=0.0 preserves the original first-change-return behaviour."""
        from gptme.tools.computer import act_and_observe

        dummy_baseline = _make_png(tmp_path, (0, 0, 0), "dummy")
        mock_transport = mock.MagicMock()
        mock_transport.screenshot.return_value = dummy_baseline

        captured_settle: list[float] = []

        def fake_poll(transport, baseline, timeout, settle_time=0.0):
            captured_settle.append(settle_time)
            return

        with (
            mock.patch(
                "gptme.tools.computer.get_transport", return_value=mock_transport
            ),
            mock.patch("gptme.tools.computer._poll_for_change", side_effect=fake_poll),
            mock.patch("gptme.tools.computer.computer", return_value=None),
        ):
            act_and_observe("left_click", coordinate=(100, 200), settle_time=0.0)

        assert captured_settle == [0.0]

    def test_window_focus_benefits_from_default_settle(self, tmp_path):
        """window_focus with default settle_time correctly waits for shell prompt.

        Simulates the multi-phase transition: xterm frame appears (first change),
        then the shell prompt renders (second change).  The test verifies that
        act_and_observe returns a message (not None) — the exact frame returned
        depends on the mock, but what matters is that the caller gets a result.
        """
        from gptme.tools.computer import act_and_observe

        dummy_baseline = _make_png(tmp_path, (0, 0, 0), "dummy")
        mock_transport = mock.MagicMock()
        mock_transport.screenshot.return_value = dummy_baseline

        sentinel_msg = mock.sentinel.screenshot_msg

        def fake_poll(transport, baseline, timeout, settle_time=0.0):
            # Verify settle_time > 0 (default) is being used
            assert settle_time > 0.0, (
                "window_focus should use settle_time > 0 so multi-phase "
                "transitions (frame→prompt) are waited for"
            )
            return sentinel_msg

        with (
            mock.patch(
                "gptme.tools.computer.get_transport", return_value=mock_transport
            ),
            mock.patch("gptme.tools.computer._poll_for_change", side_effect=fake_poll),
            mock.patch("gptme.tools.computer.computer", return_value=None),
        ):
            msgs = act_and_observe("window_focus", text="Terminal")

        assert sentinel_msg in msgs, (
            "act_and_observe('window_focus', ...) should include the settled screenshot"
        )
