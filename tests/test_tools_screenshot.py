"""Tests for the screenshot tool — screenshot capture and path validation.

Tests cover:
- _validate_screenshot_path: path traversal prevention (security-critical)
  - valid paths within OUTPUT_DIR
  - path traversal attempts (../, symlinks)
  - subdirectories within OUTPUT_DIR
- _is_available: platform-specific tool detection
- screenshot: path generation and validation integration
- tool spec: registration, name, availability
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.tools.screenshot import (
    _is_available,
    _validate_screenshot_path,
    screenshot,
    tool,
)

# ── TestValidateScreenshotPath ─────────────────────────────────────────


class TestValidateScreenshotPath:
    """Tests for _validate_screenshot_path — prevents path traversal attacks.

    Security: This function prevents arbitrary file writes via path manipulation.
    See: https://github.com/gptme/gptme/issues/1021
    """

    def test_valid_path_in_output_dir(self, tmp_path: Path, monkeypatch):
        """Accepts path directly within OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        path = tmp_path / "screenshot.png"
        result = _validate_screenshot_path(path)
        assert result == path.resolve()

    def test_valid_subdirectory(self, tmp_path: Path, monkeypatch):
        """Accepts path in subdirectory of OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        path = subdir / "screenshot.png"
        result = _validate_screenshot_path(path)
        assert result == path.resolve()

    def test_path_traversal_parent(self, tmp_path: Path, monkeypatch):
        """Rejects path that escapes OUTPUT_DIR via ../ traversal."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        malicious_path = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="must be within"):
            _validate_screenshot_path(malicious_path)

    def test_path_traversal_double_parent(self, tmp_path: Path, monkeypatch):
        """Rejects path with multiple ../ components."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        malicious_path = tmp_path / "a" / ".." / ".." / "evil.txt"
        with pytest.raises(ValueError, match="must be within"):
            _validate_screenshot_path(malicious_path)

    def test_absolute_path_outside(self, tmp_path: Path, monkeypatch):
        """Rejects absolute path outside OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        malicious_path = Path("/tmp/evil_screenshot.png")
        with pytest.raises(ValueError, match="must be within"):
            _validate_screenshot_path(malicious_path)

    def test_creates_output_dir(self, tmp_path: Path, monkeypatch):
        """Creates OUTPUT_DIR if it doesn't exist."""
        new_dir = tmp_path / "new_output"
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", new_dir)
        path = new_dir / "screenshot.png"
        result = _validate_screenshot_path(path)
        assert new_dir.exists()
        assert result == path.resolve()

    def test_symlink_escape(self, tmp_path: Path, monkeypatch):
        """Rejects symlink that resolves outside OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        # Create a symlink inside OUTPUT_DIR that points outside
        evil_target = tmp_path.parent / "evil_dir"
        evil_target.mkdir(exist_ok=True)
        symlink = tmp_path / "escape"
        symlink.symlink_to(evil_target)
        malicious_path = symlink / "screenshot.png"
        with pytest.raises(ValueError, match="must be within"):
            _validate_screenshot_path(malicious_path)

    def test_normalized_path(self, tmp_path: Path, monkeypatch):
        """Accepts path with redundant components that resolve within OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        # tmp_path/./subdir/../screenshot.png resolves to tmp_path/screenshot.png
        path = tmp_path / "." / "subdir" / ".." / "screenshot.png"
        result = _validate_screenshot_path(path)
        assert result == (tmp_path / "screenshot.png").resolve()


# ── TestIsAvailable ───────────────────────────────────────────────────────


class TestIsAvailable:
    """Tests for _is_available — platform-specific screenshot tool detection."""

    @patch("gptme.tools.screenshot.IS_MACOS", True)
    @patch("shutil.which", return_value="/usr/sbin/screencapture")
    def test_macos_with_screencapture(self, mock_which):
        """Available on macOS when screencapture is present."""
        assert _is_available() is True

    @patch("gptme.tools.screenshot.IS_MACOS", True)
    @patch("shutil.which", return_value=None)
    def test_macos_without_screencapture(self, mock_which):
        """Not available on macOS without screencapture."""
        assert _is_available() is False

    @patch("gptme.tools.screenshot.IS_MACOS", False)
    @patch("gptme.tools.screenshot.IS_WAYLAND", False)
    @patch("os.name", "posix")
    @patch("shutil.which")
    def test_linux_with_gnome_screenshot(self, mock_which):
        """Available on Linux with gnome-screenshot."""
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None
        )
        assert _is_available() is True

    @patch("gptme.tools.screenshot.IS_MACOS", False)
    @patch("gptme.tools.screenshot.IS_WAYLAND", False)
    @patch("os.name", "posix")
    @patch("shutil.which")
    def test_linux_with_scrot_x11(self, mock_which):
        """Available on Linux/X11 with scrot."""
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/scrot" if cmd == "scrot" else None
        )
        assert _is_available() is True

    @patch("gptme.tools.screenshot.IS_MACOS", False)
    @patch("gptme.tools.screenshot.IS_WAYLAND", True)
    @patch("os.name", "posix")
    @patch("shutil.which")
    def test_linux_wayland_no_scrot(self, mock_which):
        """scrot not available on Wayland (X11 only)."""
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/scrot" if cmd == "scrot" else None
        )
        assert _is_available() is False

    @patch("gptme.tools.screenshot.IS_MACOS", False)
    @patch("gptme.tools.screenshot.IS_WAYLAND", False)
    @patch("os.name", "posix")
    @patch("shutil.which", return_value=None)
    def test_linux_no_tools(self, mock_which):
        """Not available on Linux without any screenshot tools."""
        assert _is_available() is False

    @patch("gptme.tools.screenshot.IS_MACOS", False)
    @patch("os.name", "nt")
    def test_windows_not_available(self):
        """Not available on Windows (not implemented)."""
        assert _is_available() is False


# ── TestScreenshotFunction ────────────────────────────────────────────────


class TestScreenshotFunction:
    """Tests for the screenshot function — path generation and tool invocation."""

    @patch("gptme.tools.screenshot.subprocess.run")
    def test_default_path_generation(self, mock_run, tmp_path: Path, monkeypatch):
        """Generates timestamped path in OUTPUT_DIR when no path provided."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", True)
        mock_run.return_value = None

        result = screenshot()
        assert result.parent == tmp_path.resolve()
        assert result.name.startswith("screenshot_")
        assert result.suffix == ".png"

    @patch("gptme.tools.screenshot.subprocess.run")
    def test_default_path_creates_dir(self, mock_run, tmp_path: Path, monkeypatch):
        """Creates OUTPUT_DIR if it doesn't exist."""
        new_dir = tmp_path / "screenshots"
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", new_dir)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", True)
        mock_run.return_value = None

        screenshot()
        assert new_dir.exists()

    @patch("gptme.tools.screenshot.subprocess.run")
    def test_custom_path_validated(self, mock_run, tmp_path: Path, monkeypatch):
        """Custom path is validated against OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", True)
        mock_run.return_value = None

        custom = tmp_path / "my_screenshot.png"
        result = screenshot(custom)
        assert result == custom.resolve()

    def test_custom_path_traversal_rejected(self, tmp_path: Path, monkeypatch):
        """Rejects custom path that escapes OUTPUT_DIR."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        malicious = tmp_path / ".." / "evil.png"
        with pytest.raises(ValueError, match="must be within"):
            screenshot(malicious)

    @patch("gptme.tools.screenshot.subprocess.run")
    def test_macos_uses_screencapture(self, mock_run, tmp_path: Path, monkeypatch):
        """Uses screencapture command on macOS."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", True)
        mock_run.return_value = None

        screenshot()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "screencapture"

    @patch("shutil.which")
    @patch("gptme.tools.screenshot.subprocess.run")
    def test_linux_uses_gnome_screenshot(
        self, mock_run, mock_which, tmp_path: Path, monkeypatch
    ):
        """Uses gnome-screenshot on Linux when available."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", False)
        monkeypatch.setattr("gptme.tools.screenshot.IS_WAYLAND", False)
        monkeypatch.setattr("os.name", "posix")
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None
        )
        mock_run.return_value = None

        screenshot()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "gnome-screenshot"
        assert "-f" in args

    @patch("shutil.which")
    @patch("gptme.tools.screenshot.subprocess.run")
    def test_linux_uses_scrot_fallback(
        self, mock_run, mock_which, tmp_path: Path, monkeypatch
    ):
        """Falls back to scrot on Linux/X11 when gnome-screenshot unavailable."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", False)
        monkeypatch.setattr("gptme.tools.screenshot.IS_WAYLAND", False)
        monkeypatch.setattr("os.name", "posix")
        mock_which.side_effect = lambda cmd: (
            "/usr/bin/scrot" if cmd == "scrot" else None
        )
        mock_run.return_value = None

        screenshot()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "scrot"

    @patch("shutil.which", return_value=None)
    def test_linux_no_tool_raises(self, mock_which, tmp_path: Path, monkeypatch):
        """Raises NotImplementedError when no screenshot tool available."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", False)
        monkeypatch.setattr("gptme.tools.screenshot.IS_WAYLAND", False)
        monkeypatch.setattr("os.name", "posix")

        with pytest.raises(NotImplementedError, match="No supported screenshot"):
            screenshot()

    def test_windows_raises(self, tmp_path: Path, monkeypatch):
        """Raises NotImplementedError on Windows (not implemented)."""
        monkeypatch.setattr("gptme.tools.screenshot.OUTPUT_DIR", tmp_path)
        monkeypatch.setattr("gptme.tools.screenshot.IS_MACOS", False)
        monkeypatch.setattr("os.name", "nt")

        with pytest.raises(
            NotImplementedError, match="only available on macOS and Linux"
        ):
            screenshot()


# ── TestToolSpec ──────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for the screenshot tool spec configuration."""

    def test_tool_name(self):
        assert tool.name == "screenshot"

    def test_has_description(self):
        assert tool.desc
        assert "screenshot" in tool.desc.lower()

    def test_has_examples(self):
        """Tool provides usage examples."""
        assert tool.examples is not None
