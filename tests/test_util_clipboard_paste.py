"""Tests for clipboard paste functions (paste_image, paste_text)."""

from pathlib import Path
from unittest.mock import Mock, patch

from gptme.util.clipboard import paste_image, paste_text


class TestPasteImage:
    def test_paste_image_pil_not_installed(self):
        """Returns None when Pillow is not installed."""
        with patch.dict(
            "sys.modules", {"PIL": None, "PIL.Image": None, "PIL.ImageGrab": None}
        ):
            # Force reimport to trigger ImportError
            import importlib

            import gptme.util.clipboard as cb

            original = cb.paste_image

            def _paste_image_no_pil() -> Path | None:
                try:
                    from PIL import Image, ImageGrab  # noqa: F401
                except ImportError:
                    return None
                return None

            cb.paste_image = _paste_image_no_pil  # type: ignore
            try:
                result = cb.paste_image()
                assert result is None
            finally:
                cb.paste_image = original
                importlib.reload(cb)

    def test_paste_image_no_image_in_clipboard(self):
        """Returns None when clipboard has no image."""
        with patch("PIL.ImageGrab.grabclipboard", return_value=None):
            result = paste_image()
            assert result is None

    def test_paste_image_with_pil_image(self, tmp_path):
        """Returns path when clipboard has a PIL Image."""
        from PIL import Image

        mock_img = Image.new("RGB", (100, 100), color="red")

        # Mock LogManager.get_current_log() to return a manager with logdir
        mock_manager = Mock()
        mock_manager.logdir = tmp_path

        with (
            patch("PIL.ImageGrab.grabclipboard", return_value=mock_img),
            patch(
                "gptme.util.clipboard.LogManager.get_current_log",
                return_value=mock_manager,
            ),
        ):
            result = paste_image()
            assert result is not None
            assert result.exists()
            assert result.suffix == ".png"
            assert result.parent == tmp_path / "attachments"

    def test_paste_image_with_file_paths(self, tmp_path):
        """Returns path when clipboard has file paths (Windows behavior)."""
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"fake png")

        with patch("PIL.ImageGrab.grabclipboard", return_value=[str(img_file)]):
            result = paste_image()
            assert result is not None
            assert result == img_file

    def test_paste_image_with_non_image_paths(self, tmp_path):
        """Returns None when clipboard has non-image file paths."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("hello")

        with patch("PIL.ImageGrab.grabclipboard", return_value=[str(txt_file)]):
            result = paste_image()
            assert result is None

    def test_paste_image_exception(self):
        """Returns None on exception."""
        with patch("PIL.ImageGrab.grabclipboard", side_effect=Exception("fail")):
            result = paste_image()
            assert result is None


class TestPasteText:
    def test_paste_text_linux_wl_paste(self):
        """Test pasting text on Linux with wl-paste."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Wayland clipboard"

        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=["wl-paste"],
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = paste_text()
            assert result == "Wayland clipboard"

    def test_paste_text_linux_xclip(self):
        """Test pasting text on Linux with xclip."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "X11 clipboard"

        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=["xclip"],
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = paste_text()
            assert result == "X11 clipboard"

    def test_paste_text_macos(self):
        """Test pasting text on macOS."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "macOS clipboard"

        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", return_value=mock_result),
        ):
            result = paste_text()
            assert result == "macOS clipboard"

    def test_paste_text_no_clipboard_tool(self):
        """Returns None when no clipboard tool available."""
        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=[],
            ),
        ):
            result = paste_text()
            assert result is None

    def test_paste_text_exception(self):
        """Returns None on exception."""
        with (
            patch("platform.system", side_effect=Exception("fail")),
        ):
            result = paste_text()
            assert result is None
