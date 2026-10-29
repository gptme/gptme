"""Real-backend regressions for literal single-image output filenames."""

import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from gptme.tools.convert import ToolAvailability, convert_file


@pytest.mark.parametrize("backend", ["ffmpeg", "imagemagick"])
@pytest.mark.parametrize(
    "extension", ["png", "jpg", "jpeg", "bmp", "tiff", "webp", "gif"]
)
@pytest.mark.parametrize("name", ["ordinary", "literal%d", "literal%03d", "literal%"])
@pytest.mark.parametrize("existing", [False, True])
def test_image_output_filename_is_literal(
    tmp_path: Path, backend: str, extension: str, name: str, existing: bool
) -> None:
    command = "ffmpeg" if backend == "ffmpeg" else "convert"
    if not shutil.which(command):
        pytest.skip(f"{command} not installed")
    src = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(src)
    original = src.read_bytes()
    dest = tmp_path / f"{name}.{extension}"
    if existing:
        dest.write_bytes(b"old destination")
    sentinels = [
        tmp_path / f"literal{number}.{extension}" for number in ("000", "001", "0", "1")
    ]
    for sentinel in sentinels:
        sentinel.write_bytes(b"unrelated sibling")
    expected_paths = {src, dest, *sentinels}
    avail = ToolAvailability(
        ffmpeg=backend == "ffmpeg",
        imagemagick=backend == "imagemagick",
        python_magic=False,
    )
    with patch("gptme.tools.convert.get_availability", return_value=avail):
        plan = convert_file(src, dest, dry_run=True)
        assert plan.success, plan.error
        assert plan.output_path == dest
        assert set(tmp_path.iterdir()) == expected_paths - (
            {dest} if not existing else set()
        )
        if existing:
            assert dest.read_bytes() == b"old destination"
        result = convert_file(src, dest)
    assert result.success, result.error
    assert result.converter_used == backend
    assert result.output_path == dest
    assert dest.exists(), (
        f"requested path absent: {sorted(p.name for p in tmp_path.iterdir())}"
    )
    with Image.open(dest) as image:
        image.load()
        assert image.size == (16, 16)
        pixel = image.convert("RGB").getpixel((8, 8))
        assert isinstance(pixel, tuple)
        red, green, blue = pixel
        assert red > 200 and green < 40 and blue < 40
    assert set(tmp_path.iterdir()) == expected_paths
    assert src.read_bytes() == original
    for sentinel in sentinels:
        assert sentinel.read_bytes() == b"unrelated sibling"


def test_literal_image_filename_survives_backend_fallback(tmp_path: Path) -> None:
    if not shutil.which("convert"):
        pytest.skip("ImageMagick not installed")
    src = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(src)
    dest = tmp_path / "literal%03d.jpg"
    run = subprocess.run

    def fail_ffmpeg(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(
                cmd, 1, stderr=b"synthetic FFmpeg failure"
            )
        return run(cmd, **kwargs)

    with (
        patch(
            "gptme.tools.convert.get_availability",
            return_value=ToolAvailability(
                ffmpeg=True, imagemagick=True, python_magic=False
            ),
        ),
        patch("gptme.tools.convert.subprocess.run", side_effect=fail_ffmpeg),
    ):
        result = convert_file(src, dest)
    assert result.success, result.error
    assert result.converter_used == "imagemagick"
    assert set(tmp_path.iterdir()) == {src, dest}
    with Image.open(dest) as image:
        image.load()
        assert image.size == (16, 16)
