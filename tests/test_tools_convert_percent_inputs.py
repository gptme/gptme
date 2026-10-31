"""Real-FFmpeg regressions for percent-pattern image source filenames."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gptme.tools.convert import ImageConverter, ToolAvailability, convert_file


@pytest.mark.parametrize("extension", ["png", "jpg", "bmp", "tiff", "webp", "gif"])
@pytest.mark.parametrize("name", ["ordinary", "literal%d", "literal%03d", "literal%"])
@pytest.mark.parametrize("decoy", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_percent_source_is_literal(
    tmp_path: Path, extension: str, name: str, decoy: bool, direct: bool
) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    source = tmp_path / f"{name}.{extension}"
    Image.new("RGB", (16, 16), "red").save(source)
    if decoy:
        for number in ("1", "001"):
            Image.new("RGB", (16, 16), "blue").save(
                tmp_path / f"literal{number}.{extension}"
            )
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    destination = tmp_path / "output.png"
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(
            ffmpeg=True, imagemagick=False, python_magic=False
        ),
    ):
        plan = convert_file(source, destination, dry_run=True)
        assert plan.success, plan.error
        assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
        result = (ImageConverter().convert if direct else convert_file)(
            source, destination
        )
    assert result.success, result.error
    assert result.converter_used == "ffmpeg"
    assert result.output_path == destination
    with Image.open(destination) as image:
        pixel = image.convert("RGB").getpixel((8, 8))
        assert isinstance(pixel, tuple)
        red, green, blue = pixel
        assert red > 200 and green < 40 and blue < 40
    assert set(tmp_path.iterdir()) == {*before, destination}
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("source_format", ["GIF", "PNG"])
@pytest.mark.parametrize("direct", [False, True])
def test_percent_named_animation_keeps_frames(
    tmp_path: Path, source_format: str, direct: bool
) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    source = tmp_path / f"animated%03d.{source_format.lower()}"
    Image.new("RGB", (16, 16), "red").save(
        source,
        save_all=True,
        append_images=[Image.new("RGB", (16, 16), "blue")],
        duration=100,
        loop=0,
    )
    original = source.read_bytes()
    destination = tmp_path / "output.gif"
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(
            ffmpeg=True, imagemagick=False, python_magic=False
        ),
    ):
        result = (ImageConverter().convert if direct else convert_file)(
            source, destination
        )
    assert result.success, result.error
    with Image.open(destination) as image:
        assert getattr(image, "n_frames", 1) == 2
        for index, expected in enumerate(((255, 0, 0), (0, 0, 255))):
            image.seek(index)
            pixel = image.convert("RGB").getpixel((8, 8))
            assert isinstance(pixel, tuple)
            assert all(
                abs(actual - target) < 10 for actual, target in zip(pixel, expected)
            )
    assert source.read_bytes() == original


@pytest.mark.parametrize("name", ["ordinary.png", "literal%03d.png"])
@pytest.mark.parametrize("path_entry", ["./bin", "."])
def test_image_conversion_with_relative_executable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, path_entry: str
) -> None:
    """Resolve FFmpeg from the caller's PATH before changing its working directory."""
    import gptme.tools.convert as conversion

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed")
    executable = Path(ffmpeg).absolute()
    monkeypatch.chdir(tmp_path)
    binary_directory = tmp_path / path_entry
    binary_directory.mkdir(exist_ok=True)
    (binary_directory / "ffmpeg").symlink_to(executable)
    monkeypatch.setenv("PATH", path_entry)
    monkeypatch.setattr(conversion, "_tool_avail", None)
    assert conversion.get_availability().ffmpeg
    source = Path(name)
    Image.new("RGB", (16, 16), "red").save(source)
    original = source.read_bytes()
    destination = Path("output.png")

    result = ImageConverter().convert(source, destination)

    assert result.success, result.error
    assert result.converter_used == "ffmpeg"
    assert result.output_path == destination
    assert source.read_bytes() == original
    with Image.open(destination) as image:
        assert image.convert("RGB").getpixel((8, 8)) == (255, 0, 0)


@pytest.mark.parametrize("fail_copy", [False, True])
def test_percent_temporary_input_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_copy: bool
) -> None:
    """Relative paths survive a percent-bearing temp parent, including copy failure."""
    import gptme.tools.convert as conversion

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg not installed")
    monkeypatch.chdir(tmp_path)
    source = Path("-literal%03d.png")
    Image.new("RGB", (16, 16), "red").save(source)
    original = source.read_bytes()
    destination = Path("output%03d.png")
    parent = tmp_path / "private%03d"
    parent.mkdir()
    monkeypatch.setattr(conversion.tempfile, "tempdir", str(parent))
    if fail_copy:

        def partial_copy(src: Path, dest: Path) -> None:
            dest.write_bytes(b"partial temporary copy")
            raise OSError("synthetic disk full")

        monkeypatch.setattr(conversion.shutil, "copyfile", partial_copy)
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(
            ffmpeg=True, imagemagick=False, python_magic=False
        ),
    ):
        result = ImageConverter().convert(source, destination)
    assert list(parent.iterdir()) == []
    assert source.read_bytes() == original
    if fail_copy:
        assert not result.success
        assert result.error and "synthetic disk full" in result.error
        assert not destination.exists()
    else:
        assert result.success, result.error
        assert result.output_path == destination
        with Image.open(destination) as image:
            assert image.convert("RGB").getpixel((8, 8)) == (255, 0, 0)
