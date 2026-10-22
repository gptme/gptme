"""Tests for clipboard functions (copy, paste_image, paste_text)."""

from pathlib import Path
from unittest.mock import Mock, patch

from gptme.util.clipboard import copy, paste_image, paste_text, set_copytext


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

            cb.paste_image = _paste_image_no_pil
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


class TestCopy:
    def test_copy_linux_wl_copy(self):
        """Copies via wl-copy on Linux."""
        set_copytext("hello wayland")
        mock_result = Mock()
        mock_result.returncode = 0

        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=["wl-copy"],
            ),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            assert copy() is True
            mock_run.assert_called_once()

    def test_copy_linux_xclip(self):
        """Copies via xclip on Linux."""
        set_copytext("hello x11")
        mock_result = Mock()
        mock_result.returncode = 0

        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=["xclip"],
            ),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert copy() is True

    def test_copy_linux_no_tool(self):
        """Returns False on Linux when no clipboard utility is installed."""
        set_copytext("nowhere to go")
        with (
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.util.clipboard.get_installed_programs",
                return_value=[],
            ),
        ):
            assert copy() is False

    def test_copy_macos(self):
        """Copies via pbcopy on macOS."""
        set_copytext("hello mac")
        mock_result = Mock()
        mock_result.returncode = 0

        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", return_value=mock_result),
        ):
            assert copy() is True

    def _windows_ctypes(self, *, open_ok=True, alloc_ok=True, set_ok=True):
        """Build a fake ctypes module exposing a Windows clipboard API.

        ``ctypes.windll`` only exists on Windows, so the whole surface the
        Windows branch touches is faked here, letting the test run on any OS.
        """
        import ctypes as real_ctypes

        user32 = Mock()
        kernel32 = Mock()
        user32.OpenClipboard.return_value = 1 if open_ok else 0
        kernel32.GlobalAlloc.return_value = 0x1000 if alloc_ok else 0
        kernel32.GlobalLock.return_value = 0x2000
        user32.SetClipboardData.return_value = 0x1000 if set_ok else 0

        fake = Mock()
        fake.windll.user32 = user32
        fake.windll.kernel32 = kernel32
        fake.create_unicode_buffer = real_ctypes.create_unicode_buffer
        fake.sizeof = real_ctypes.sizeof
        fake.memmove = Mock()
        fake.c_void_p = real_ctypes.c_void_p
        fake.c_size_t = real_ctypes.c_size_t
        fake.wintypes = real_ctypes.wintypes
        return fake, user32, kernel32

    def test_copy_windows_success(self):
        """Copies via the Win32 clipboard API and reports success."""
        set_copytext("hello 世界")
        fake, user32, kernel32 = self._windows_ctypes()

        with (
            patch("platform.system", return_value="Windows"),
            patch.dict("sys.modules", {"ctypes": fake}),
        ):
            assert copy() is True

        user32.SetClipboardData.assert_called_once()
        # Success transfers ownership to the OS; the handle must not be freed.
        kernel32.GlobalFree.assert_not_called()
        user32.CloseClipboard.assert_called_once()

    def test_copy_windows_open_failure(self):
        """Returns False when the clipboard cannot be opened."""
        set_copytext("blocked")
        fake, user32, kernel32 = self._windows_ctypes(open_ok=False)

        with (
            patch("platform.system", return_value="Windows"),
            patch.dict("sys.modules", {"ctypes": fake}),
        ):
            assert copy() is False

        kernel32.GlobalAlloc.assert_not_called()
        user32.CloseClipboard.assert_not_called()

    def test_copy_windows_set_failure_frees_handle(self):
        """Frees the handle and closes the clipboard when SetClipboardData fails."""
        set_copytext("will fail")
        fake, user32, kernel32 = self._windows_ctypes(set_ok=False)

        with (
            patch("platform.system", return_value="Windows"),
            patch.dict("sys.modules", {"ctypes": fake}),
        ):
            assert copy() is False

        # On failure ownership stays with us, so the handle must be freed.
        kernel32.GlobalFree.assert_called_once()
        user32.CloseClipboard.assert_called_once()

    def test_copy_unsupported_platform(self):
        """Returns False on an unrecognized platform."""
        set_copytext("nope")
        with patch("platform.system", return_value="Plan9"):
            assert copy() is False
