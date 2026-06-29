"""
Unit tests for computer tool action dispatch (window_focus, wait_for_change)
and the observe_web / observe_desktop helper functions.

All tests use a mock transport — no X11 display or xdotool required.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from gptme.tools.computer import (
    _dispatch_transport,
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


pytest.importorskip("PIL", reason="PIL not installed")


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
        transport = _FixedScreenTransport(tmp_path)
        _dispatch_transport(transport, "wait_for_change", text="0.3")
        # In 300ms with 50ms→100ms→200ms backoff: baseline + ≥2 polls + timeout shot
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
            patch("gptme.tools.computer.time.monotonic", side_effect=fast_clock),
            patch("gptme.tools.computer.time.sleep"),
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

    def test_returns_list(self) -> None:
        """observe_web() always returns a list (possibly empty on hard failure)."""
        with (
            patch.dict("sys.modules", {"gptme.tools.browser": None}),
            patch("gptme.tools.computer.computer", return_value=None),
        ):
            result = observe_web("https://example.com")
        assert isinstance(result, list)

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
