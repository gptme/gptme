"""Tests for the vision tool (view_image function)."""

from pathlib import Path

import pytest
from PIL import Image

from gptme.tools.vision import view_image


@pytest.fixture
def small_png(tmp_path: Path) -> Path:
    """Create a small PNG image (well under 1MB)."""
    img = Image.new("RGB", (100, 100), color="red")
    path = tmp_path / "small.png"
    img.save(str(path))
    return path


@pytest.fixture
def small_rgba_png(tmp_path: Path) -> Path:
    """Create a small RGBA PNG image."""
    img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
    path = tmp_path / "rgba.png"
    img.save(str(path))
    return path


@pytest.fixture
def large_bmp(tmp_path: Path) -> Path:
    """Create a large BMP image (over 1MB) — uncompressed format guarantees size."""
    # 800x800 RGB BMP = ~1.9MB uncompressed
    img = Image.new("RGB", (800, 800))
    pixels = img.load()
    assert pixels is not None
    for x in range(800):
        for y in range(800):
            pixels[x, y] = (
                (x * 7 + y * 13) % 256,
                (x * 11 + y * 3) % 256,
                (x * 5 + y * 17) % 256,
            )
    path = tmp_path / "large.bmp"
    img.save(str(path))
    assert path.stat().st_size > 1024 * 1024, (
        f"Expected >1MB, got {path.stat().st_size}"
    )
    return path


@pytest.fixture
def huge_bmp(tmp_path: Path) -> Path:
    """Create a very large BMP image (several MB) that needs both compression and scaling."""
    import os

    # 2000x2000 RGB BMP = ~12MB uncompressed — triggers both compression and scaling
    # Random noise is incompressible as JPEG (still >1MB after compression, forces scaling)
    # os.urandom + frombytes is far faster than per-pixel loops (~0.1s vs 10+s)
    data = os.urandom(2000 * 2000 * 3)
    img = Image.frombytes("RGB", (2000, 2000), data)
    path = tmp_path / "huge.bmp"
    img.save(str(path))
    return path


class TestViewImageSmall:
    """Tests for images under 1MB (no scaling needed)."""

    def test_small_image_no_scaling(self, small_png: Path):
        """Small image should be returned without scaling."""
        msg = view_image(small_png)
        assert msg.role == "system"
        assert "No scaling required" in msg.content
        assert "under 1MB" in msg.content
        assert len(msg.files) == 1
        assert msg.files[0] == small_png.absolute()

    def test_small_image_dimensions_reported(self, small_png: Path):
        """Message should include image dimensions."""
        msg = view_image(small_png)
        assert "100x100" in msg.content

    def test_string_path_input(self, small_png: Path):
        """String paths should work the same as Path objects."""
        msg = view_image(str(small_png))
        assert msg.role == "system"
        assert "No scaling required" in msg.content
        assert len(msg.files) == 1

    def test_rgba_image_small(self, small_rgba_png: Path):
        """Small RGBA images should work without conversion (under 1MB)."""
        msg = view_image(small_rgba_png)
        assert msg.role == "system"
        assert "No scaling required" in msg.content
        assert len(msg.files) == 1


class TestViewImageLarge:
    """Tests for images over 1MB (compression/scaling needed)."""

    def test_large_image_compressed(self, large_bmp: Path):
        """Large image should be compressed to JPEG."""
        msg = view_image(large_bmp)
        assert msg.role == "system"
        assert "compressed" in msg.content.lower()
        assert len(msg.files) == 1
        # Output file should be a JPEG
        out_file = msg.files[0]
        assert isinstance(out_file, Path)
        assert out_file.suffix == ".jpg"

    def test_large_image_under_limit(self, large_bmp: Path):
        """Compressed output should be under 1MB."""
        msg = view_image(large_bmp)
        out_file = msg.files[0]
        assert isinstance(out_file, Path)
        output_size = out_file.stat().st_size
        assert output_size <= 1024 * 1024, (
            f"Output {output_size} bytes exceeds 1MB limit"
        )

    def test_huge_image_scaled(self, huge_bmp: Path):
        """Very large images should be both compressed and scaled."""
        msg = view_image(huge_bmp)
        assert msg.role == "system"
        assert "scaled" in msg.content.lower() or "Scaling" in msg.content
        assert len(msg.files) == 1
        # Output should be under 1MB
        out_file = msg.files[0]
        assert isinstance(out_file, Path)
        output_size = out_file.stat().st_size
        assert output_size <= 1.5 * 1024 * 1024, (
            f"Output {output_size} bytes still too large"
        )


class TestViewImagePIL:
    """Tests for PIL Image object input."""

    def test_pil_image_input(self):
        """PIL Image objects should be handled correctly."""
        img = Image.new("RGB", (50, 50), color="blue")
        msg = view_image(img)
        assert msg.role == "system"
        assert "No scaling required" in msg.content
        assert len(msg.files) == 1
        # Should have saved to a temp file
        out_file = msg.files[0]
        assert isinstance(out_file, Path)
        assert out_file.exists()

    def test_pil_rgba_image_input(self):
        """PIL RGBA Image objects should work."""
        img = Image.new("RGBA", (50, 50), color=(0, 255, 0, 128))
        msg = view_image(img)
        assert msg.role == "system"
        assert len(msg.files) == 1


class TestViewImageErrors:
    """Tests for error handling."""

    def test_nonexistent_file(self, tmp_path: Path):
        """Non-existent path should return error message."""
        msg = view_image(tmp_path / "does_not_exist.png")
        assert msg.role == "system"
        assert "not found" in msg.content.lower()
        assert len(msg.files) == 0

    def test_nonexistent_string_path(self, tmp_path: Path):
        """Non-existent string path should return error message."""
        msg = view_image(str(tmp_path / "nonexistent.png"))
        assert msg.role == "system"
        assert "not found" in msg.content.lower()


class TestViewImageFormats:
    """Tests for different image formats."""

    def test_jpeg_input(self, tmp_path: Path):
        """JPEG images should work."""
        img = Image.new("RGB", (100, 100), color="green")
        path = tmp_path / "test.jpg"
        img.save(str(path), "JPEG")
        msg = view_image(path)
        assert msg.role == "system"
        assert len(msg.files) == 1

    def test_grayscale_image(self, tmp_path: Path):
        """Grayscale (mode 'L') images should work."""
        img = Image.new("L", (100, 100), color=128)
        path = tmp_path / "gray.png"
        img.save(str(path))
        msg = view_image(path)
        assert msg.role == "system"
        assert len(msg.files) == 1

    def test_palette_image(self, tmp_path: Path):
        """Palette mode ('P') images should work."""
        img = Image.new("P", (100, 100))
        path = tmp_path / "palette.png"
        img.save(str(path))
        msg = view_image(path)
        assert msg.role == "system"
        assert len(msg.files) == 1
