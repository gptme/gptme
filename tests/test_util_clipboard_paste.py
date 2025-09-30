"""Tests for clipboard paste utilities."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from gptme.util.clipboard import paste_image, paste_text


class TestPasteImage:
    """Test clipboard image pasting."""

    def test_paste_image_with_pil_image(self, tmp_path):
        """Test pasting a PIL Image from clipboard."""
        # Create a test image
        test_img = Image.new("RGB", (100, 100), color="red")

        # Mock PIL's ImageGrab.grabclipboard
        with patch("PIL.ImageGrab.grabclipboard", return_value=test_img):
            result = paste_image(attachments_dir=tmp_path)

            assert result is not None
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".png"
            assert result.parent == tmp_path

            # Verify the saved image
            with Image.open(result) as img:
                assert img.size == (100, 100)

    def test_paste_image_no_attachments_dir(self):
        """Test pasting image without attachments directory (uses temp file)."""
        test_img = Image.new("RGB", (50, 50), color="blue")

        with patch("PIL.ImageGrab.grabclipboard", return_value=test_img):
            result = paste_image()

            assert result is not None
            assert isinstance(result, Path)
            assert result.exists()
            assert result.suffix == ".png"

            # Cleanup
            result.unlink()

    def test_paste_image_none(self):
        """Test when no image in clipboard."""
        with patch("PIL.ImageGrab.grabclipboard", return_value=None):
            result = paste_image()
            assert result is None

    def test_paste_image_file_path_list(self):
        """Test when clipboard contains file path list (Windows behavior)."""
        # Create a temporary image file
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            test_img = Image.new("RGB", (50, 50), color="green")
            test_img.save(tmp.name)
            tmp_path = tmp.name

        try:
            # Mock ImageGrab to return a list of file paths (Windows behavior)
            with patch("PIL.ImageGrab.grabclipboard", return_value=[tmp_path]):
                result = paste_image()
                assert result is not None
                assert isinstance(result, str)
                assert Path(result).exists()
        finally:
            Path(tmp_path).unlink()

    def test_paste_image_file_path_list_non_image(self):
        """Test when clipboard contains non-image file paths."""
        # Create a temporary text file
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            tmp.write(b"test")
            tmp_path = tmp.name

        try:
            with patch("PIL.ImageGrab.grabclipboard", return_value=[tmp_path]):
                result = paste_image()
                assert result is None
        finally:
            Path(tmp_path).unlink()

    def test_paste_image_exception(self):
        """Test error handling when paste fails."""
        with patch("PIL.ImageGrab.grabclipboard", side_effect=Exception("Test error")):
            result = paste_image()
            assert result is None


class TestPasteText:
    """Test clipboard text pasting."""

    def test_paste_text_linux_xclip(self):
        """Test pasting text on Linux with xclip."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Test clipboard content"

        with patch("platform.system", return_value="Linux"):
            with patch("gptme.util.get_installed_programs", return_value=["xclip"]):
                with patch("subprocess.run", return_value=mock_result):
                    result = paste_text()
                    assert result == "Test clipboard content"

    def test_paste_text_linux_wl_paste(self):
        """Test pasting text on Linux with wl-paste."""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Wayland clipboard"

        with patch("platform.system", return_value="Linux"):
            with patch("gptme.util.get_installed_programs", return_value=["wl-paste"]):
                with patch("subprocess.run", return_value=mock_result):
                    result = paste_text()
                    assert result == "Wayland clipboard"

    def test_paste_text_exception(self):
        """Test error handling when paste fails."""
        with patch("platform.system", side_effect=Exception("Test error")):
            result = paste_text()
            assert result is None
