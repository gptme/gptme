"""Real-backend TIFF document page preservation; no subprocess substitutes."""

from __future__ import annotations

import shutil
import struct
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from PIL import Image, ImageSequence

from gptme.tools.convert import ImageConverter, ToolAvailability, convert_file

if TYPE_CHECKING:
    from pathlib import Path

COLORS = [(255, 0, 0), (0, 0, 255), (0, 128, 0)]


@pytest.mark.parametrize("backend", ["ffmpeg", "imagemagick", "both"])
@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("source_suffix", [".tif", ".tiff", ".bin"])
def test_tiff_pages(
    tmp_path: Path, backend: str, count: int, existing: bool, source_suffix: str
) -> None:
    if backend != "imagemagick" and not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    if backend != "ffmpeg" and not shutil.which("convert"):
        pytest.skip("ImageMagick not installed")
    source = tmp_path / f"document{source_suffix}"
    destination = tmp_path / "requested.tiff"
    frames = [Image.new("RGB", (16, 16), color) for color in COLORS[:count]]
    frames[0].save(source, format="TIFF", save_all=True, append_images=frames[1:])
    before = source.read_bytes()
    sentinel = b"previous destination"
    if existing:
        destination.write_bytes(sentinel)
    availability = ToolAvailability(
        ffmpeg=backend != "imagemagick", imagemagick=backend != "ffmpeg"
    )
    with patch("gptme.tools.convert.get_availability", return_value=availability):
        result = (
            ImageConverter().convert(source, destination)
            if source_suffix == ".bin"
            else convert_file(source, destination)
        )
    assert source.read_bytes() == before
    if count > 1 and backend == "ffmpeg":
        assert not result.success, "FFmpeg must not report success after dropping pages"
        assert result.error and "multipage TIFF" in result.error
        assert (
            destination.read_bytes() == sentinel
            if existing
            else not destination.exists()
        )
    else:
        assert result.success, result.error
        with Image.open(destination) as image:
            pixels = [
                frame.convert("RGB").getpixel((0, 0))
                for frame in ImageSequence.Iterator(image)
            ]
        assert pixels == COLORS[:count]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        [source.name] + ([destination.name] if destination.exists() else [])
    )


@pytest.mark.parametrize("backend", ["ffmpeg", "imagemagick", "both"])
def test_tiff_page_inspection_failure(tmp_path: Path, backend: str) -> None:
    """An unfamiliar later-page tag must not turn an unknown count into one page."""
    if not shutil.which("convert") or not shutil.which("ffmpeg"):
        pytest.skip("real FFmpeg and ImageMagick required")
    source = tmp_path / "private-tag.tiff"
    first = Image.new("RGB", (16, 16), COLORS[0])
    second = Image.new("RGB", (16, 16), COLORS[1])
    first.save(source, save_all=True, append_images=[second], tiffinfo={0xBC01: 1})
    destination = tmp_path / "output.tiff"
    availability = ToolAvailability(
        ffmpeg=backend != "imagemagick", imagemagick=backend != "ffmpeg"
    )
    with patch("gptme.tools.convert.get_availability", return_value=availability):
        result = convert_file(source, destination)
    if backend == "ffmpeg":
        assert not result.success
        assert not destination.exists()
    else:
        assert result.success, result.error
        with Image.open(destination) as image:
            assert [
                frame.convert("RGB").getpixel((0, 0))
                for frame in ImageSequence.Iterator(image)
            ] == COLORS[:2]


def _heterogeneous_tiff() -> bytes:
    """Two baseline IFDs: RGB8 red followed by RGB32 blue (unsupported by Pillow)."""
    data = bytearray(b"II" + struct.pack("<HI", 42, 8))
    for page, bits in enumerate((8, 32)):
        extra = len(data) + 2 + 12 * 11 + 4
        color = (255, 0, 0) if page == 0 else (0, 0, (1 << bits) - 1)
        pixels = struct.pack("<BBB" if bits == 8 else "<III", *color)
        entries = [
            (256, 4, 1, 1),  # width
            (257, 4, 1, 1),  # height
            (258, 3, 3, extra),  # bits per sample
            (259, 3, 1, 1),  # uncompressed
            (262, 3, 1, 2),  # RGB
            (273, 4, 1, extra + 6),  # strip offset
            (277, 3, 1, 3),  # samples per pixel
            (278, 4, 1, 1),  # rows per strip
            (279, 4, 1, len(pixels)),
            (284, 3, 1, 1),  # chunky
            (339, 3, 1, 1),  # unsigned integer
        ]
        end = extra + 6 + len(pixels)
        data += struct.pack("<H", len(entries))
        data += b"".join(struct.pack("<HHII", *entry) for entry in entries)
        data += struct.pack("<I", end + end % 2 if page == 0 else 0)
        data += struct.pack("<HHH", bits, bits, bits) + pixels
        if len(data) % 2:
            data += b"\0"
    return bytes(data)


@pytest.mark.parametrize("backend", ["ffmpeg", "imagemagick", "both"])
def test_tiff_unsupported_later_pixel_mode(tmp_path: Path, backend: str) -> None:
    if not shutil.which("convert") or not shutil.which("ffmpeg"):
        pytest.skip("real FFmpeg and ImageMagick required")
    source = tmp_path / "heterogeneous.tiff"
    source.write_bytes(_heterogeneous_tiff())
    destination = tmp_path / "output.tiff"
    availability = ToolAvailability(
        ffmpeg=backend != "imagemagick", imagemagick=backend != "ffmpeg"
    )
    with patch("gptme.tools.convert.get_availability", return_value=availability):
        result = convert_file(source, destination)
    if backend == "ffmpeg":
        assert not result.success
        assert not destination.exists()
    else:
        assert result.success, result.error
        # ImageMagick preserves RGB32, which Pillow cannot decode. Verify both
        # pages through a capable decoder rather than weakening the fixture.
        decoded = subprocess.run(
            ["convert", str(destination), "-depth", "8", "rgb:-"],
            capture_output=True,
            check=True,
        )
        assert decoded.stdout == bytes([255, 0, 0, 0, 0, 255])
    assert source.read_bytes() == _heterogeneous_tiff()


@pytest.mark.parametrize("backend", ["ffmpeg", "imagemagick", "both"])
@pytest.mark.parametrize("existing", [False, True])
def test_tiff_size_limit_inspection_failure(
    tmp_path: Path, backend: str, existing: bool
) -> None:
    """Pillow's size guard must not crash conversion or permit page loss."""
    if not shutil.which("convert") or not shutil.which("ffmpeg"):
        pytest.skip("real FFmpeg and ImageMagick required")
    source = tmp_path / "size-limited.tiff"
    first = Image.new("RGB", (16, 16), COLORS[0])
    second = Image.new("RGB", (32, 32), COLORS[1])
    first.save(source, save_all=True, append_images=[second])
    before = source.read_bytes()
    destination = tmp_path / "output.tiff"
    sentinel = b"previous destination"
    if existing:
        destination.write_bytes(sentinel)
    availability = ToolAvailability(
        ffmpeg=backend != "imagemagick", imagemagick=backend != "ffmpeg"
    )
    limit = 100
    with patch.object(Image, "MAX_IMAGE_PIXELS", limit):
        # Exercise the actual exception rather than substituting Image.open or
        # allocating hundreds of megapixels.
        with pytest.raises(Image.DecompressionBombError), Image.open(source):
            pass
        with patch("gptme.tools.convert.get_availability", return_value=availability):
            result = convert_file(source, destination)
        assert Image.MAX_IMAGE_PIXELS == 100
    assert source.read_bytes() == before
    if backend == "ffmpeg":
        assert not result.success
        assert result.error and "multipage TIFF" in result.error
        assert (
            destination.read_bytes() == sentinel
            if existing
            else not destination.exists()
        )
    else:
        assert result.success, result.error
        assert result.converter_used == "imagemagick"
        with Image.open(destination) as image:
            assert [
                (frame.size, frame.convert("RGB").getpixel((0, 0)))
                for frame in ImageSequence.Iterator(image)
            ] == [((16, 16), COLORS[0]), ((32, 32), COLORS[1])]
