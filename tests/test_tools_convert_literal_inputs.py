"""Real-FFmpeg checks that local source paths select the intended file."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gptme.tools.convert import (
    ImageConverter,
    ToolAvailability,
    VideoThumbnailConverter,
    convert_file,
)


@pytest.fixture(scope="module")
def source_media(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    root = tmp_path_factory.mktemp("literal-input-media")
    media = {}
    for color in ("red", "blue"):
        image = root / f"{color}.png"
        Image.new("RGB", (32, 32), color).save(image)
        video = root / f"{color}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(image),
                "-c:v",
                "mpeg4",
                str(video),
            ],
            capture_output=True,
            check=True,
        )
        media[f"{color}.png"] = image.read_bytes()
        media[f"{color}.mp4"] = video.read_bytes()
    return media


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("name", ["file:control", "ordinary", "-source"])
@pytest.mark.parametrize("absolute", [False, True])
@pytest.mark.parametrize("decoy", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_local_source_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_media: dict[str, bytes],
    kind: str,
    name: str,
    absolute: bool,
    decoy: bool,
    direct: bool,
) -> None:
    if os.name == "nt" and ":" in name:
        pytest.skip("Windows filenames cannot contain colons")
    monkeypatch.chdir(tmp_path)
    extension = "png" if kind == "image" else "mp4"
    src = Path(f"{name}.{extension}")
    src.write_bytes(source_media[f"red.{extension}"])
    if decoy:
        Path(f"control.{extension}").write_bytes(source_media[f"blue.{extension}"])
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    if absolute:
        src = tmp_path / src
    dest = tmp_path / "result.png"
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(
            ffmpeg=True, imagemagick=False, python_magic=False
        ),
    ):
        if direct:
            converter = (
                ImageConverter() if kind == "image" else VideoThumbnailConverter()
            )
            result = converter.convert(src, dest)
        else:
            plan = convert_file(src, dest, dry_run=True)
            assert plan.success, plan.error
            assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
            result = convert_file(src, dest)
    assert result.success, result.error
    assert result.converter_used == "ffmpeg"
    assert result.output_path == dest
    with Image.open(dest) as image:
        pixel = image.convert("RGB").getpixel((16, 16))
        assert isinstance(pixel, tuple)
        red, green, blue = pixel
        assert red > 200 and green < 40 and blue < 40
    assert set(tmp_path.iterdir()) == {*before, dest}
    assert all(path.read_bytes() == content for path, content in before.items())
