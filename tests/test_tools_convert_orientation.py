"""Real-backend regressions for displayed image orientation (issue #4625)."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
from typing import TYPE_CHECKING

import pytest
from PIL import Image, ImageOps, JpegImagePlugin

if TYPE_CHECKING:
    from pathlib import Path

import gptme.tools.convert as convert_module
from gptme.tools.convert import ToolAvailability, convert_file


@pytest.fixture
def imagemagick_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Select the real ImageMagick backend without replacing subprocess.run."""
    if not shutil.which("convert"):
        pytest.skip("ImageMagick convert is not installed")
    monkeypatch.setattr(
        convert_module,
        "_tool_avail",
        ToolAvailability(ffmpeg=False, imagemagick=True, python_magic=False),
    )


def _source(path: Path, orientation: int | None) -> bytes:
    """A nonsquare image with four distinct quadrants detects flips and turns."""
    image = Image.new("RGB", (96, 64))
    for box, color in (
        ((0, 0, 48, 32), (220, 30, 40)),
        ((48, 0, 96, 32), (30, 190, 50)),
        ((0, 32, 48, 64), (40, 60, 210)),
        ((48, 32, 96, 64), (230, 200, 30)),
    ):
        image.paste(color, box)
    if orientation is None:
        image.save(path, quality=98, subsampling=0)
    else:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(path, quality=98, subsampling=0, exif=exif)
    return path.read_bytes()


def _assert_displayed_image(src: Path, dest: Path) -> None:
    with Image.open(src) as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")
    with Image.open(dest) as output:
        # Check before TIFF's loader can apply orientation itself. Normalized
        # pixels must not carry a stale tag that rotates them a second time.
        assert output.getexif().get(274, 1) == 1
        actual = output.convert("RGB")
    assert actual.size == expected.size
    # Sample well inside each quadrant: JPEG recompression/subsampling can
    # introduce noise at the quadrant boundaries, unrelated to orientation.
    for x in (expected.width // 4, 3 * expected.width // 4):
        for y in (expected.height // 4, 3 * expected.height // 4):
            assert actual.getpixel((x, y)) == pytest.approx(
                expected.getpixel((x, y)), abs=12
            ), f"wrong displayed quadrant at {(x, y)}"


@pytest.mark.parametrize("extension", ["bmp", "png", "jpg", "tiff"])
@pytest.mark.parametrize("orientation", [None, *range(1, 9)])
def test_imagemagick_displayed_orientation(
    tmp_path: Path,
    imagemagick_only: None,
    orientation: int | None,
    extension: str,
) -> None:
    src = tmp_path / "source.jpg"
    before = _source(src, orientation)
    dest = tmp_path / f"output.{extension}"

    result = convert_file(src, dest, quality="high")

    assert src.read_bytes() == before
    assert result.success, result.error
    assert result.converter_used == "imagemagick"
    assert result.output_path == dest
    assert result.lossy is (extension == "jpg")
    _assert_displayed_image(src, dest)


def test_orientation_dry_run(tmp_path: Path, imagemagick_only: None) -> None:
    src = tmp_path / "source.jpg"
    before = _source(src, 6)
    dest = tmp_path / "not-created" / "output.png"

    result = convert_file(src, dest, dry_run=True)

    assert result.success
    assert result.converter_used == "image"
    assert result.metadata["dry_run"] is True
    assert src.read_bytes() == before
    assert not dest.exists()
    assert not dest.parent.exists()


def test_orientation_literal_filename(tmp_path: Path, imagemagick_only: None) -> None:
    src = tmp_path / "source [0] photo.jpg"
    before = _source(src, 6)
    dest = tmp_path / "output [1] photo.png"

    result = convert_file(src, dest)

    assert src.read_bytes() == before
    assert result.success, result.error
    assert result.converter_used == "imagemagick"
    assert dest.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([src.name, dest.name])
    _assert_displayed_image(src, dest)


def test_orientation_jpeg_quality(tmp_path: Path, imagemagick_only: None) -> None:
    src = tmp_path / "source.jpg"
    before = _source(src, 6)
    low = tmp_path / "low.jpg"
    high = tmp_path / "high.jpg"

    for quality, dest in (("low", low), ("high", high)):
        result = convert_file(src, dest, quality=quality)
        assert result.success, result.error
        assert result.lossy
        assert result.converter_used == "imagemagick"
    assert src.read_bytes() == before
    # Quantization tables are a direct quality signal, unlike file size which
    # also depends on metadata and compression choices.
    with Image.open(low) as low_image, Image.open(high) as high_image:
        assert isinstance(low_image, JpegImagePlugin.JpegImageFile)
        assert isinstance(high_image, JpegImagePlugin.JpegImageFile)
        assert sum(low_image.quantization[0]) > sum(high_image.quantization[0])
    _assert_displayed_image(src, low)
    _assert_displayed_image(src, high)


def test_ffmpeg_primary_orientation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")
    monkeypatch.setattr(
        convert_module,
        "_tool_avail",
        ToolAvailability(ffmpeg=True, imagemagick=False, python_magic=False),
    )
    src = tmp_path / "source.jpg"
    before = _source(src, 6)
    dest = tmp_path / "output.png"

    result = convert_file(src, dest)

    assert src.read_bytes() == before
    assert result.success, result.error
    assert result.converter_used == "ffmpeg"
    _assert_displayed_image(src, dest)


def test_ffmpeg_failure_falls_back_with_orientation(
    tmp_path: Path,
    imagemagick_only: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is not installed")
    # Run the real executable with an invalid input decoder, so FFmpeg fails
    # for real and exercises the ImageMagick fallback (no fake exit-only shim).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "ffmpeg"
    wrapper.write_text(
        f'#!/bin/sh\nexec {shlex.quote(ffmpeg)} -c:v gptme-no-such-decoder "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setattr(
        convert_module,
        "_tool_avail",
        ToolAvailability(ffmpeg=True, imagemagick=True, python_magic=False),
    )
    src = tmp_path / "source.jpg"
    before = _source(src, 6)
    dest = tmp_path / "output.png"

    with caplog.at_level(logging.WARNING, logger=convert_module.__name__):
        result = convert_file(src, dest)

    assert src.read_bytes() == before
    assert "ffmpeg image convert failed" in caplog.text
    assert "Unknown decoder 'gptme-no-such-decoder'" in caplog.text
    assert result.success, result.error
    assert result.converter_used == "imagemagick"
    _assert_displayed_image(src, dest)
