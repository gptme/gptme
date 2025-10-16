"""Tests for clipboard utilities."""

from gptme.util.clipboard import is_image_path


def test_is_image_path_valid_png(tmp_path):
    """Test detection of valid PNG image path."""
    # Create a temporary PNG file
    img_file = tmp_path / "test.png"
    img_file.write_text("fake png data")

    assert is_image_path(str(img_file))


def test_is_image_path_valid_jpg(tmp_path):
    """Test detection of valid JPG image path."""
    # Create a temporary JPG file
    img_file = tmp_path / "test.jpg"
    img_file.write_text("fake jpg data")

    assert is_image_path(str(img_file))


def test_is_image_path_various_extensions(tmp_path):
    """Test detection of various image extensions."""
    extensions = [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"]

    for ext in extensions:
        img_file = tmp_path / f"test{ext}"
        img_file.write_text("fake image data")
        assert is_image_path(str(img_file)), f"Failed for extension {ext}"


def test_is_image_path_with_quotes(tmp_path):
    """Test detection of image path with quotes."""
    img_file = tmp_path / "test.png"
    img_file.write_text("fake png data")

    # Test with double quotes
    assert is_image_path(f'"{img_file}"')

    # Test with single quotes
    assert is_image_path(f"'{img_file}'")


def test_is_image_path_with_whitespace(tmp_path):
    """Test detection of image path with leading/trailing whitespace."""
    img_file = tmp_path / "test.png"
    img_file.write_text("fake png data")

    assert is_image_path(f"  {img_file}  ")


def test_is_image_path_non_image_file(tmp_path):
    """Test that non-image files are not detected as images."""
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("not an image")

    assert not is_image_path(str(txt_file))


def test_is_image_path_nonexistent_file():
    """Test that nonexistent files are not detected as images."""
    assert not is_image_path("/nonexistent/path/to/image.png")


def test_is_image_path_empty_string():
    """Test that empty string is not detected as image."""
    assert not is_image_path("")


def test_is_image_path_directory(tmp_path):
    """Test that directories are not detected as images."""
    img_dir = tmp_path / "images"
    img_dir.mkdir()

    assert not is_image_path(str(img_dir))


def test_is_image_path_case_insensitive(tmp_path):
    """Test that extension matching is case-insensitive."""
    # Create files with uppercase extensions
    img_file = tmp_path / "test.PNG"
    img_file.write_text("fake png data")

    assert is_image_path(str(img_file))

    img_file2 = tmp_path / "test.JPG"
    img_file2.write_text("fake jpg data")

    assert is_image_path(str(img_file2))


def test_is_image_path_invalid_input():
    """Test that invalid inputs are handled gracefully."""
    # None
    assert not is_image_path(None)  # type: ignore

    # Non-path strings
    assert not is_image_path("not a path")
    assert not is_image_path("just text")
