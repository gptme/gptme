"""ImageMagick must not parse the staging parent's scene/frame syntax."""

import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

from gptme.tools.convert import ImageConverter, PDFToImageConverter, ToolAvailability


@pytest.mark.parametrize("converter", [ImageConverter, PDFToImageConverter])
@pytest.mark.parametrize("temp_name", ["job%d", "job[0]"])
def test_imagemagick_staging_parent_is_literal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    converter: type[ImageConverter] | type[PDFToImageConverter],
    temp_name: str,
) -> None:
    executable = shutil.which("convert")
    if not executable:
        pytest.skip("ImageMagick not installed")
    # Exercise relative PATH resolution as well as relative source/destination
    # paths when the converter changes only its child's working directory.
    monkeypatch.chdir(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    local_executable = bin_dir / Path(executable).name
    shutil.copyfile(executable, local_executable)
    shutil.copymode(executable, local_executable)
    # No absolute fallback: losing the original relative lookup must fail.
    monkeypatch.setenv("PATH", "bin")
    temp_root = tmp_path / temp_name
    temp_root.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(temp_root))
    monkeypatch.setattr(
        "gptme.tools.convert.get_availability",
        lambda: ToolAvailability(ffmpeg=False, pdftoppm=False, imagemagick=True),
    )
    src = Path("image%d.png")
    Image.new("RGB", (20, 10), "red").save(src)
    original = src.read_bytes()
    Image.new("RGB", (50, 50), "blue").save("image0.png")
    dest = Path("output.jpg")

    # PNG input intentionally exercises the PDF caller's real ImageMagick
    # command without requiring Ghostscript or a PDF-enabled security policy.
    result = converter().convert(src, dest)

    assert result.success, result.error
    assert result.output_path == dest
    assert result.converter_used == "imagemagick"
    with Image.open(dest) as output:
        assert output.size == (20, 10)
    assert Path.cwd() == tmp_path
    assert src.read_bytes() == original
    assert list(temp_root.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixtures")
@pytest.mark.parametrize("converter", [ImageConverter, PDFToImageConverter])
@pytest.mark.parametrize("path_entry", ["bin", ".", ""])
def test_imagemagick_staging_preserves_delegate_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    converter: type[ImageConverter] | type[PDFToImageConverter],
    path_entry: str,
) -> None:
    executable = shutil.which("convert")
    if not executable:
        pytest.skip("ImageMagick not installed")
    executable = str(Path(executable).absolute())
    monkeypatch.chdir(tmp_path)
    bin_dir = tmp_path / path_entry
    bin_dir.mkdir(exist_ok=True)
    marker = tmp_path / "delegate-ran"
    # A real child lookup models ImageMagick finding gs, without depending on
    # the host's PDF security policy or Ghostscript installation.
    python_executable = tmp_path / "python with spaces"
    python_executable.symlink_to(sys.executable)
    delegate = bin_dir / "gptme-test-delegate"
    delegate_code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    delegate.write_text(
        "#!/bin/sh\n"
        f"exec {shlex.quote(str(python_executable))} -c {shlex.quote(delegate_code)}\n"
    )
    delegate.chmod(0o755)
    wrapper = bin_dir / "convert"
    wrapper.write_text(
        "#!/bin/sh\n"
        "gptme-test-delegate || exit $?\n"
        f'exec {shlex.quote(executable)} "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", path_entry)
    monkeypatch.setattr(
        "gptme.tools.convert.get_availability",
        lambda: ToolAvailability(ffmpeg=False, pdftoppm=False, imagemagick=True),
    )
    src = Path("image[0].png")
    Image.new("RGB", (20, 10), "red").save(src)
    dest = Path("output.jpg")

    result = converter().convert(src, dest)

    assert result.success, result.error
    assert marker.is_file(), "the selected convert must find its delegate"
    with Image.open(dest) as output:
        assert output.size == (20, 10)
    assert os.environ["PATH"] == path_entry
    assert Path.cwd() == tmp_path
