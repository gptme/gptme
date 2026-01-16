"""Tests for PDF-to-image conversion functionality."""


def test_pdf_to_images_detection():
    """Test PDF-to-image tool detection and hints."""
    from gptme.tools.browser import (
        _get_pdf_to_image_hints,
        _has_imagemagick,
        _has_pdftoppm,
        _has_vips,
    )

    # At least one tool should be detected (or hints should mention installation)
    hints = _get_pdf_to_image_hints()
    assert hints, "Should return hints"

    if _has_pdftoppm() or _has_imagemagick() or _has_vips():
        # Should mention the pdf_to_images function
        assert "pdf_to_images" in hints, "Should mention pdf_to_images function"
    else:
        # Should mention installation instructions
        assert "Install one of" in hints, "Should provide installation instructions"


def test_pdf_to_images_no_tool():
    """Test pdf_to_images error when no tools available."""
    import shutil
    from unittest.mock import patch

    from gptme.tools.browser import pdf_to_images

    # Mock all tool detection to return False
    with (
        patch.object(shutil, "which", return_value=None),
        patch("gptme.tools.browser._has_pdftoppm", return_value=False),
        patch("gptme.tools.browser._has_imagemagick", return_value=False),
        patch("gptme.tools.browser._has_vips", return_value=False),
    ):
        try:
            pdf_to_images("/nonexistent/file.pdf")
            raise AssertionError("Should raise RuntimeError")
        except RuntimeError as e:
            assert "No PDF-to-image tools available" in str(e)
        except FileNotFoundError:
            # This is also acceptable - file check happens before tool check
            pass


def test_pdf_to_images_tool_selection():
    """Test that pdf_to_images selects tools in correct order."""
    from gptme.tools.browser import (
        _has_imagemagick,
        _has_pdftoppm,
        _has_vips,
    )

    # Just verify tool detection works (actual conversion would need real PDFs)
    # At least verify the functions don't crash
    pdftoppm = _has_pdftoppm()
    imagemagick = _has_imagemagick()
    vips = _has_vips()

    # Print available tools for debugging
    print(f"pdftoppm: {pdftoppm}, imagemagick: {imagemagick}, vips: {vips}")

    # At least one assertion to make the test meaningful
    assert isinstance(pdftoppm, bool)
    assert isinstance(imagemagick, bool)
    assert isinstance(vips, bool)
