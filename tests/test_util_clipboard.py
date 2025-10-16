"""Tests for clipboard utilities."""

import tempfile
from unittest.mock import patch

from PIL import Image

from gptme.util.clipboard import get_clipboard_image, get_clipboard_text


class TestClipboardImage:
    """Test clipboard image handling."""

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_image_with_pil_image(self, mock_pyclip):
        """Test getting PIL Image from clipboard."""
        # Create a test image
        test_img = Image.new("RGB", (100, 100), color="red")
        mock_pyclip.paste_image.return_value = test_img

        result = get_clipboard_image()

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"

        # Verify the saved image
        with Image.open(result) as img:
            assert img.size == (100, 100)

        # Cleanup
        result.unlink()

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_image_with_bytes(self, mock_pyclip):
        """Test getting raw bytes from clipboard."""
        # Create test image bytes
        test_img = Image.new("RGB", (50, 50), color="blue")
        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            test_img.save(tmp.name)
            with open(tmp.name, "rb") as f:
                test_bytes = f.read()

        mock_pyclip.paste_image.return_value = test_bytes

        result = get_clipboard_image()

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"

        # Cleanup
        result.unlink()

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_image_none(self, mock_pyclip):
        """Test when no image in clipboard."""
        mock_pyclip.paste_image.return_value = None

        result = get_clipboard_image()

        assert result is None

    def test_get_clipboard_image_no_pyclip(self):
        """Test when pyclip is not available."""
        with patch("gptme.util.clipboard.pyclip", None):
            with patch.dict("sys.modules", {"pyclip": None}):
                result = get_clipboard_image()
                assert result is None


class TestClipboardText:
    """Test clipboard text handling."""

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_text_string(self, mock_pyclip):
        """Test getting string from clipboard."""
        mock_pyclip.paste.return_value = "Hello, World!"

        result = get_clipboard_text()

        assert result == "Hello, World!"

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_text_bytes(self, mock_pyclip):
        """Test getting bytes from clipboard."""
        mock_pyclip.paste.return_value = b"Hello, Bytes!"

        result = get_clipboard_text()

        assert result == "Hello, Bytes!"

    @patch("gptme.util.clipboard.pyclip")
    def test_get_clipboard_text_none(self, mock_pyclip):
        """Test when no text in clipboard."""
        mock_pyclip.paste.return_value = None

        result = get_clipboard_text()

        assert result is None

    def test_get_clipboard_text_no_pyclip(self):
        """Test when pyclip is not available."""
        with patch("gptme.util.clipboard.pyclip", None):
            with patch.dict("sys.modules", {"pyclip": None}):
                result = get_clipboard_text()
                assert result is None
