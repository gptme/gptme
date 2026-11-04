"""Real-FFmpeg regressions for literal, single-frame video thumbnails."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gptme.tools.convert import ToolAvailability, convert_file


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    src = tmp_path_factory.mktemp("thumbnail-video") / "source.mp4"
    # Distinct first/last frames detect an accidental last-frame overwrite.
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:d=0.2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "mpeg4",
            str(src),
        ],
        capture_output=True,
        check=True,
    )
    return src


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "png"])
@pytest.mark.parametrize("name", ["ordinary", "literal%d", "literal%03d", "literal%"])
@pytest.mark.parametrize("existing", [False, True])
def test_video_thumbnail_filename_is_literal(
    tmp_path: Path, video: Path, extension: str, name: str, existing: bool
) -> None:
    original = video.read_bytes()
    dest = tmp_path / f"{name}.{extension}"
    if existing:
        dest.write_bytes(b"old destination")
    sentinels = [tmp_path / f"literal{number}.{extension}" for number in ("001", "1")]
    for sentinel in sentinels:
        sentinel.write_bytes(b"unrelated sibling")
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(ffmpeg=True, python_magic=False),
    ):
        plan = convert_file(video, dest, dry_run=True)
        assert plan.success, plan.error
        assert plan.output_path == dest
        assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
        result = convert_file(video, dest)
    assert result.success, result.error
    assert result.converter_used == "ffmpeg"
    assert result.output_path == dest
    assert dest.exists(), f"requested path absent: {list(tmp_path.iterdir())}"
    with Image.open(dest) as image:
        image.load()
        assert image.size == (32, 32)
        pixel = image.convert("RGB").getpixel((16, 16))
        assert isinstance(pixel, tuple)
        red, green, blue = pixel
        assert red > 200 and green < 40 and blue < 40
        assert getattr(image, "n_frames", 1) == 1
    assert set(tmp_path.iterdir()) == {dest, *sentinels}
    assert video.read_bytes() == original
    for sentinel in sentinels:
        assert sentinel.read_bytes() == b"unrelated sibling"


@pytest.mark.parametrize(
    "audio_only", [False, True], ids=["invalid", "no-video-stream"]
)
def test_video_thumbnail_missing_video_fails(tmp_path: Path, audio_only: bool) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    src = tmp_path / "source.mp4"
    if audio_only:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.2",
                "-c:a",
                "aac",
                str(src),
            ],
            capture_output=True,
            check=True,
        )
    else:
        src.write_bytes(b"not a video")
    dest = tmp_path / "literal%03d.png"
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(ffmpeg=True, python_magic=False),
    ):
        result = convert_file(src, dest)
    assert not result.success
    assert result.error
    assert result.output_path is None
    assert set(tmp_path.iterdir()) == {src}
