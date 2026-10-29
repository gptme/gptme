"""Displayed-pixel orientation survives the large-image attachment boundary."""

import hashlib
import random
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from gptme.tools import vision


@pytest.fixture(autouse=True)
def isolated_temp_files(monkeypatch: pytest.MonkeyPatch):
    paths: list[Path] = []
    monkeypatch.setattr(vision, "_temp_files", paths)
    yield
    for path in paths:
        path.unlink(missing_ok=True)


def asymmetric_image(resize: bool) -> Image.Image:
    size = (1400, 2100) if resize else (800, 1200)
    image = (
        Image.frombytes(
            "RGB", size, random.Random(123).randbytes(size[0] * size[1] * 3)
        )
        if resize
        else Image.new("RGB", size, "white")
    )
    width, height = size
    # Big asymmetric corner patches remain identifiable after JPEG and resize.
    for box, color in [
        ((0, 0, width // 4, height // 4), "red"),
        ((3 * width // 4, 0, width, height // 4), "green"),
        ((0, 3 * height // 4, width // 4, height), "blue"),
        ((3 * width // 4, 3 * height // 4, width, height), "yellow"),
    ]:
        image.paste(color, box)
    return image


@pytest.mark.parametrize("orientation", [None, 1, 2, 3, 4, 5, 6, 7, 8])
@pytest.mark.parametrize("resize", [False, True])
def test_large_exif_display_pixels(
    tmp_path: Path, orientation: int | None, resize: bool
) -> None:
    source = tmp_path / "oriented.png"
    image = asymmetric_image(resize)
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    exif[315] = "Synthetic private author"
    image.save(source, compress_level=0, exif=exif)
    assert source.stat().st_size > 1024 * 1024
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    with Image.open(source) as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")

    message = vision.view_image(source)
    assert len(message.files) == 1
    attachment = message.files[0]
    assert isinstance(attachment, Path)
    assert attachment in vision._temp_files
    with Image.open(attachment) as actual:
        assert actual.getexif().get(274) is None
        assert actual.getexif().get(315) is None
        if resize:
            assert actual.width < expected.width
            assert actual.height < expected.height
            assert "Scaling from" in message.content
        else:
            assert actual.size == expected.size
            assert "Scaling from" not in message.content
        assert f"({actual.width}x{actual.height})" in message.content
        assert (
            abs(actual.width / actual.height - expected.width / expected.height) < 0.01
        )
        for fx, fy in [(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)]:
            wanted = expected.getpixel(
                (int(expected.width * fx), int(expected.height * fy))
            )
            got = actual.getpixel((int(actual.width * fx), int(actual.height * fy)))
            assert isinstance(wanted, tuple) and isinstance(got, tuple)
            assert all(abs(a - b) < 15 for a, b in zip(wanted, got)), (wanted, got)
    assert hashlib.sha256(source.read_bytes()).digest() == original_hash


@pytest.mark.parametrize("orientation", [2, 6, 8])
def test_small_exif_original_is_untouched(tmp_path: Path, orientation: int) -> None:
    source = tmp_path / "small.jpg"
    exif = Image.Exif()
    exif[274] = orientation
    Image.new("RGB", (80, 120), "red").save(source, exif=exif)
    original = source.read_bytes()
    message = vision.view_image(source)
    assert message.files == [source]
    assert "No scaling required" in message.content
    assert vision._temp_files == []
    assert source.read_bytes() == original
    with Image.open(source) as image:
        assert image.getexif().get(274) == orientation
