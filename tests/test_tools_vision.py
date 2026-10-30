"""Tests for the vision tool (view_image function)."""

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, PngImagePlugin

from gptme.tools import vision
from gptme.tools.vision import view_image


@pytest.fixture(autouse=True)
def isolated_temp_files(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    paths: list[Path] = []
    monkeypatch.setattr(vision, "_temp_files", paths)
    yield
    for path in paths:
        path.unlink(missing_ok=True)


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


class TestViewImageModes:
    @pytest.fixture(autouse=True)
    def isolated_temp_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Iterator[None]:
        monkeypatch.setattr(vision, "_temp_files", [])
        monkeypatch.setattr(vision.tempfile, "tempdir", str(tmp_path))
        yield
        vision._cleanup_temp_files()

    @pytest.mark.parametrize("large", [False, True])
    @pytest.mark.parametrize("transparency", [None, 0, bytes([0, 128])])
    def test_palette_png(
        self, tmp_path: Path, large: bool, transparency: int | bytes | None
    ) -> None:
        img = Image.new("P", (1200, 1000))
        img.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        img.paste(1, (600, 0, 1200, 1000))
        if transparency is not None:
            img.info["transparency"] = transparency
        source = tmp_path / "palette.png"
        img.save(source, compress_level=0 if large else 9)
        original = source.read_bytes()
        assert (len(original) > 1024 * 1024) == large

        msg = view_image(source)

        assert len(msg.files) == 1
        output = msg.files[0]
        assert isinstance(output, Path)
        with Image.open(output) as attached:
            attached.load()
            assert attached.size == img.size
            assert attached.format == ("JPEG" if large else "PNG")
            assert attached.mode == ("RGB" if large else "P")
            if large:
                # JPEG drops alpha, retaining palette colors even at alpha 0/128,
                # consistently with the existing RGBA compression policy.
                for point, expected in [
                    ((300, 500), (255, 0, 0)),
                    ((900, 500), (0, 0, 255)),
                ]:
                    actual = attached.getpixel(point)
                    assert isinstance(actual, tuple)
                    assert all(abs(a - b) <= 3 for a, b in zip(actual, expected))
            elif transparency is not None:
                assert attached.info["transparency"] == transparency

        assert source.read_bytes() == original
        assert vision._temp_files == ([output] if large else [])
        assert set(tmp_path.iterdir()) == {source, output}
        vision._cleanup_temp_files()
        assert set(tmp_path.iterdir()) == {source}

    @pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "1", "LA"])
    def test_large_ordinary_modes(self, tmp_path: Path, mode: str) -> None:
        # Mode 1 needs >8 million pixels to exceed 1 MiB even uncompressed.
        img = Image.new(mode, (3000, 3000), "white")
        source = tmp_path / "ordinary.png"
        img.save(source, compress_level=0)
        original = source.read_bytes()
        assert len(original) > 1024 * 1024

        msg = view_image(source)

        assert len(msg.files) == 1
        output = msg.files[0]
        assert isinstance(output, Path)
        with Image.open(output) as attached:
            attached.load()
            assert attached.format == "JPEG"
            assert attached.size == img.size
            assert attached.convert("RGB").getpixel((1500, 1500)) == (255, 255, 255)
        assert source.read_bytes() == original
        assert vision._temp_files == [output]
        assert set(tmp_path.iterdir()) == {source, output}
        vision._cleanup_temp_files()
        assert set(tmp_path.iterdir()) == {source}


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

    def test_path_outside_allowed_roots_is_denied(self, tmp_path: Path):
        """Absolute paths outside workspace/temp/screenshot output are refused."""
        outside = Path("/etc/passwd")
        msg = view_image(outside)
        assert msg.role == "system"
        assert "outside allowed directories" in msg.content.lower()
        assert len(msg.files) == 0

    def test_outside_path_does_not_leak_existence(self):
        """Boundary check runs before exists(), so missing out-of-bound files
        get the same denial as present ones."""
        missing = Path("/etc/gptme-vision-does-not-exist.png")
        msg = view_image(missing)
        assert "outside allowed directories" in msg.content.lower()
        assert "not found" not in msg.content.lower()
        assert len(msg.files) == 0


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


@pytest.mark.parametrize("size", [(128, 66000), (66000, 128)])
def test_oversized_png_constrained_before_jpeg(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    """Real RGB PNGs must be resized before the first JPEG encoder call."""
    source = tmp_path / "oversized.png"
    Image.new("RGB", size, "red").save(source, compress_level=0)
    assert source.stat().st_size > 1024 * 1024
    original_hash = hashlib.sha256(source.read_bytes()).digest()

    message = view_image(source)

    assert len(message.files) == 1
    attachment = message.files[0]
    assert isinstance(attachment, Path)
    assert attachment != source
    assert attachment in vision._temp_files
    with Image.open(attachment) as actual:
        actual.load()
        assert actual.format == "JPEG"
        assert actual.mode == "RGB"
        assert max(actual.size) == 65500
        assert min(actual.size) > 0
        scale = 65500 / max(size)
        assert all(abs(got - want * scale) <= 1 for got, want in zip(actual.size, size))
        assert f"({actual.width}x{actual.height})" in message.content
    assert "scaled and compressed" in message.content
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash


@pytest.mark.parametrize("length", [65500, 65501])
def test_jpeg_dimension_boundary(tmp_path: Path, length: int) -> None:
    source = tmp_path / "boundary.png"
    Image.new("RGB", (length, 8), "blue").save(source, compress_level=0)
    assert source.stat().st_size > 1024 * 1024
    message = view_image(source)
    attachment = message.files[0]
    assert isinstance(attachment, Path)
    with Image.open(attachment) as actual:
        actual.load()
        assert actual.size == (65500, 8)
    assert ("Scaling from" in message.content) == (length > 65500)


@pytest.mark.parametrize("size", [(1, 66000), (66000, 1)])
def test_small_oversized_png_passthrough(tmp_path: Path, size: tuple[int, int]) -> None:
    source = tmp_path / "small-oversized.png"
    Image.new("RGB", size, "red").save(source)
    original = source.read_bytes()
    assert len(original) < 1024 * 1024
    message = view_image(source)
    assert message.files == [source]
    assert "No scaling required" in message.content
    assert vision._temp_files == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("size", [(1, 65500), (65500, 1)])
def test_byte_resize_keeps_thin_dimension_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    source = tmp_path / "thin.png"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("padding", "x" * (1024 * 1024))
    Image.new("RGB", size, "red").save(source, pnginfo=metadata)
    original = source.read_bytes()
    save = Image.Image.save
    saves: list[tuple[int, int]] = []

    def padded_save(
        image: Image.Image, fp: str | Path, *args: Any, **kwargs: Any
    ) -> None:
        save(image, fp, *args, **kwargs)
        saves.append(image.size)
        if len(saves) == 1:
            # Force the byte-based branch without a giant/noisy fixture. JPEG
            # allows trailing bytes; both saves still exercise the real encoder.
            with Path(fp).open("ab") as output:
                output.write(b"\0" * (2 * 1024 * 1024))

    monkeypatch.setattr(Image.Image, "save", padded_save)
    message = view_image(source)
    assert len(saves) == 2
    attachment = message.files[0]
    assert isinstance(attachment, Path)
    assert attachment in vision._temp_files
    with Image.open(attachment) as actual:
        actual.load()
        assert min(actual.size) == 1
        assert 0 < max(actual.size) < 65500
    assert source.read_bytes() == original


@pytest.mark.parametrize("failing_save", [1, 2])
def test_jpeg_failure_removes_temporary_output(
    monkeypatch: pytest.MonkeyPatch, large_bmp: Path, failing_save: int
) -> None:
    save = Image.Image.save
    outputs: list[Path] = []
    original = large_bmp.read_bytes()

    def failing_encoder(
        image: Image.Image, fp: str | Path, *args: Any, **kwargs: Any
    ) -> None:
        outputs.append(Path(fp))
        if len(outputs) == failing_save:
            Path(fp).write_bytes(b"partial JPEG")
            raise OSError("encoder failed")
        save(image, fp, *args, **kwargs)
        with Path(fp).open("ab") as output:
            output.write(b"\0" * (2 * 1024 * 1024))

    monkeypatch.setattr(Image.Image, "save", failing_encoder)
    with pytest.raises(OSError, match="encoder failed"):
        view_image(large_bmp)
    assert len(outputs) == failing_save
    assert all(not path.exists() for path in outputs)
    assert vision._temp_files == []
    assert large_bmp.read_bytes() == original
