"""Failure-safe publication at both public conversion entry points."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.tools import convert as conversion


@pytest.mark.parametrize("entry", ["router", "direct"])
def test_legacy_converter_override_remains_usable(tmp_path, entry):
    class LegacyConverter(conversion.Converter):
        name = "legacy"
        supported = [("text/plain", "md")]

        def is_available(self, avail):
            return True

        def convert(self, src, dest, quality="medium", **kwargs):
            assert quality == "high"
            assert kwargs == {"custom_option": "retained"}
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            return conversion.ConversionResult(True, dest, self.name)

    source, destination = tmp_path / "source.txt", tmp_path / "output.md"
    source.write_text("legacy converter å", encoding="utf-8")
    converter = LegacyConverter()
    with (
        patch.object(conversion, "_CONVERTERS", [converter]),
        patch.object(conversion, "_detect_mime", return_value="text/plain"),
    ):
        result = (conversion.convert_file if entry == "router" else converter.convert)(
            source, destination, quality="high", custom_option="retained"
        )
    assert result.success, result.error
    assert result.converter_used == "legacy"
    assert result.output_path == destination
    assert destination.read_bytes() == source.read_bytes()
    assert set(tmp_path.iterdir()) == {source, destination}


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("entry", ["router", "direct"])
def test_real_docx_write_failure_preserves_destination(tmp_path, existing, entry):
    docx = pytest.importorskip("docx")
    pytest.importorskip("resource")
    source = tmp_path / "source.docx"
    document = docx.Document()
    document.add_paragraph("X" * 4096)
    document.save(source)
    original = source.read_bytes()
    destination = tmp_path / "output.txt"
    sentinel = b"valuable existing output\n"
    if existing:
        destination.write_bytes(sentinel)
    # Only the disposable child gets the limit; creating the DOCX needs more bytes.
    script = """
import json, resource, signal, sys
from pathlib import Path
from unittest.mock import patch
from gptme.tools import convert as conversion
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
resource.setrlimit(resource.RLIMIT_FSIZE, (128, 128))
source, destination = map(Path, sys.argv[1:3])
avail = conversion.ToolAvailability(python_docx=True, python_magic=False, libreoffice=False)
with patch.object(conversion, 'get_availability', return_value=avail):
    call = conversion.convert_file if sys.argv[3] == 'router' else conversion.DocumentToTextConverter().convert
    result = call(source, destination)
print(json.dumps({'success': result.success, 'error': result.error}))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(source), str(destination), entry],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    result = json.loads(child.stdout)
    assert not result["success"], result
    assert result["error"], result
    assert source.read_bytes() == original
    if existing:
        assert destination.read_bytes() == sentinel
    else:
        assert not destination.exists()
    assert set(tmp_path.iterdir()) == {source} | ({destination} if existing else set())


# Each external backend writes actual bytes, then reports failure or raises.
BACKENDS = [
    (conversion.PDFToImageConverter, "pdftoppm", ".pdf", ".png"),
    (conversion.PDFToImageConverter, "imagemagick", ".pdf", ".png"),
    (conversion.ImageConverter, "ffmpeg", ".png", ".jpg"),
    (conversion.ImageConverter, "imagemagick", ".png", ".webp"),
    (conversion.PDFToTextConverter, "pdftotext", ".pdf", ".txt"),
    (conversion.DocumentToTextConverter, "libreoffice", ".doc", ".txt"),
    (conversion.VideoThumbnailConverter, "ffmpeg", ".mp4", ".jpg"),
]


def availability(backend):
    return conversion.ToolAvailability(
        **{
            name: name == backend
            for name in conversion.ToolAvailability.__dataclass_fields__
        }
    )


def backend_output(command):
    if command[0] == "libreoffice":
        return Path(command[command.index("--outdir") + 1]) / "source.txt"
    if command[0] == "pdftoppm":
        return Path(command[-1] + "-1.png")
    return Path(command[-1])


@pytest.mark.parametrize(("converter", "backend", "source_ext", "dest_ext"), BACKENDS)
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["return", "exception"])
def test_backend_partial_failure_is_not_published(
    tmp_path, converter, backend, source_ext, dest_ext, existing, failure
):
    source = tmp_path / f"source{source_ext}"
    source.write_bytes(b"unchanged source")
    destination = tmp_path / f"output{dest_ext}"
    if existing:
        destination.write_bytes(b"sentinel")
    outputs = []

    def run(command, **kwargs):
        output = backend_output(command)
        outputs.append(output)
        output.write_bytes(b"partial conversion")
        if failure == "exception":
            raise OSError("backend launch/read failure")
        return subprocess.CompletedProcess(command, 1, stderr=b"backend failed")

    with (
        patch.object(
            conversion, "get_availability", return_value=availability(backend)
        ),
        patch.object(conversion.subprocess, "run", side_effect=run),
    ):
        result = converter().convert(source, destination)
    assert not result.success
    assert result.error
    assert outputs
    assert source.read_bytes() == b"unchanged source"
    if existing:
        assert destination.read_bytes() == b"sentinel"
    else:
        assert not destination.exists()
    assert set(tmp_path.iterdir()) == {source} | ({destination} if existing else set())
    assert all(not output.exists() for output in outputs)


@pytest.mark.parametrize(("converter", "backend", "source_ext", "dest_ext"), BACKENDS)
@pytest.mark.parametrize("symlink", [False, True])
def test_backend_success_publishes_requested_path(
    tmp_path, converter, backend, source_ext, dest_ext, symlink
):
    source = tmp_path / f"source{source_ext}"
    source.write_bytes(b"unchanged source")
    destination = tmp_path / f"literal%03d[1]{dest_ext}"
    target = tmp_path / "target.other-extension"
    if symlink:
        target.write_bytes(b"sentinel")
        try:
            destination.symlink_to(target.name)
        except OSError:
            pytest.skip("symlinks unavailable")
    else:
        destination.write_bytes(b"sentinel")
    original_entries = set(tmp_path.iterdir())
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        backend_output(command).write_bytes(b"complete output\r\n\xc3\xa5")
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    with (
        patch.object(
            conversion, "get_availability", return_value=availability(backend)
        ),
        patch.object(conversion.subprocess, "run", side_effect=run),
    ):
        result = converter().convert(source, destination, quality="high")
    assert result.success, result.error
    assert result.output_path == destination
    assert destination.read_bytes() == b"complete output\r\n\xc3\xa5"
    assert source.read_bytes() == b"unchanged source"
    assert destination.is_symlink() == symlink
    if symlink:
        assert target.read_bytes() == destination.read_bytes()
    assert set(tmp_path.iterdir()) == original_entries
    assert commands


@pytest.mark.parametrize("resolve_error", [OSError, RuntimeError])
def test_destination_resolution_failure_returns_result(tmp_path, resolve_error):
    source, destination = tmp_path / "source.mp4", tmp_path / "output.jpg"
    source.write_bytes(b"source")
    with (
        patch.object(Path, "resolve", side_effect=resolve_error("symlink loop")),
        patch.object(conversion.VideoThumbnailConverter, "_convert") as backend,
    ):
        result = conversion.VideoThumbnailConverter().convert(source, destination)
    assert not result.success
    assert result.output_path is None
    assert "symlink loop" in (result.error or "")
    backend.assert_not_called()
    assert set(tmp_path.iterdir()) == {source}


def test_symlink_loop_destination_returns_failure(tmp_path):
    source, destination = tmp_path / "source.mp4", tmp_path / "output.jpg"
    source.write_bytes(b"source")
    try:
        destination.symlink_to(destination.name)
    except OSError:
        pytest.skip("symlinks unavailable")
    with patch.object(conversion.VideoThumbnailConverter, "_convert") as backend:
        result = conversion.VideoThumbnailConverter().convert(source, destination)
    assert not result.success
    assert result.output_path is None
    assert result.error
    backend.assert_not_called()
    assert destination.readlink() == Path(destination.name)
    assert source.read_bytes() == b"source"
    assert set(tmp_path.iterdir()) == {source, destination}


@pytest.mark.parametrize("existing", [False, True])
def test_publication_failure_preserves_destination(tmp_path, existing):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    destination = tmp_path / "output.jpg"
    if existing:
        destination.write_bytes(b"sentinel")

    def run(command, **kwargs):
        Path(command[-1]).write_bytes(b"complete")
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    with (
        patch.object(
            conversion, "get_availability", return_value=availability("ffmpeg")
        ),
        patch.object(conversion.subprocess, "run", side_effect=run),
        patch.object(Path, "replace", side_effect=OSError("publication refused")),
    ):
        result = conversion.VideoThumbnailConverter().convert(source, destination)
    assert not result.success
    assert "publication refused" in (result.error or "")
    if existing:
        assert destination.read_bytes() == b"sentinel"
    else:
        assert not destination.exists()
    assert set(tmp_path.iterdir()) == {source} | ({destination} if existing else set())


def test_success_without_output_does_not_publish_stale_destination(tmp_path):
    source, destination = tmp_path / "source.mp4", tmp_path / "out.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"sentinel")
    with (
        patch.object(
            conversion, "get_availability", return_value=availability("ffmpeg")
        ),
        patch.object(
            conversion.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ),
    ):
        result = conversion.VideoThumbnailConverter().convert(source, destination)
    assert not result.success
    assert destination.read_bytes() == b"sentinel"
    assert set(tmp_path.iterdir()) == {source, destination}


def test_failed_primary_output_cannot_masquerade_as_fallback_success(tmp_path):
    source, destination = tmp_path / "source.png", tmp_path / "output.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"sentinel")
    avail = availability("ffmpeg")
    avail.imagemagick = True

    def run(command, **kwargs):
        if command[0] == "ffmpeg":
            Path(command[-1]).write_bytes(b"failed primary bytes")
            return subprocess.CompletedProcess(command, 1, stderr=b"failed")
        # A zero status alone must not recycle the failed attempt's output.
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    with (
        patch.object(conversion, "get_availability", return_value=avail),
        patch.object(conversion.subprocess, "run", side_effect=run),
    ):
        result = conversion.convert_file(source, destination)
    assert not result.success
    assert destination.read_bytes() == b"sentinel"
    assert set(tmp_path.iterdir()) == {source, destination}


def test_cleanup_error_after_publication_keeps_success(tmp_path):
    source, destination = tmp_path / "source.mp4", tmp_path / "output.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"sentinel")
    cleanup = conversion.tempfile.TemporaryDirectory.cleanup

    def cleanup_then_raise(directory):
        cleanup(directory)
        raise OSError("cleanup failed after commit")

    def run(command, **kwargs):
        Path(command[-1]).write_bytes(b"complete")
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    with (
        patch.object(
            conversion, "get_availability", return_value=availability("ffmpeg")
        ),
        patch.object(conversion.subprocess, "run", side_effect=run),
        patch.object(
            conversion.tempfile.TemporaryDirectory, "cleanup", cleanup_then_raise
        ),
    ):
        result = conversion.VideoThumbnailConverter().convert(source, destination)
    assert result.success, result.error
    assert result.output_path == destination
    assert any("cleanup" in warning.lower() for warning in result.warnings)
    assert destination.read_bytes() == b"complete"
    assert set(tmp_path.iterdir()) == {source, destination}


@pytest.mark.parametrize("extension", ["txt", "md"])
@pytest.mark.parametrize("existing", [False, True])
def test_real_docx_success_and_private_mode(tmp_path, extension, existing):
    docx = pytest.importorskip("docx")
    source = tmp_path / "source.docx"
    document = docx.Document()
    document.add_paragraph("ordinary Unicode å control")
    document.save(source)
    original = source.read_bytes()
    destination = tmp_path / f"output.{extension}"
    if existing:
        destination.write_bytes(b"sentinel")
        destination.chmod(0o600)
    with patch.object(
        conversion, "get_availability", return_value=availability("python_docx")
    ):
        result = conversion.convert_file(source, destination)
    assert result.success, result.error
    assert result.output_path == destination
    assert destination.read_text(encoding="utf-8") == "ordinary Unicode å control"
    if existing and os.name == "posix":
        assert destination.stat().st_mode & 0o777 == 0o600
    assert source.read_bytes() == original
    assert set(tmp_path.iterdir()) == {source, destination}


@pytest.mark.parametrize("existing", [False, True])
def test_pypdf_partial_write_is_not_published(tmp_path, existing):
    source, destination = tmp_path / "source.pdf", tmp_path / "output.txt"
    source.write_bytes(b"synthetic extraction boundary")
    if existing:
        destination.write_bytes(b"sentinel")
    write_text = Path.write_text

    def fail_write(path, text, **kwargs):
        write_text(path, text[:3], **kwargs)
        raise OSError("injected partial write")

    from types import SimpleNamespace

    reader = SimpleNamespace(
        pages=[SimpleNamespace(extract_text=lambda: "complete extracted text")]
    )
    with (
        patch.object(
            conversion, "get_availability", return_value=availability("pypdf")
        ),
        patch("pypdf.PdfReader", return_value=reader),
        patch.object(Path, "write_text", fail_write),
    ):
        result = conversion.convert_file(source, destination)
    assert not result.success
    assert "injected partial write" in (result.error or "")
    if existing:
        assert destination.read_bytes() == b"sentinel"
    else:
        assert not destination.exists()
    assert set(tmp_path.iterdir()) == {source} | ({destination} if existing else set())


def test_interrupt_cleans_staging_without_touching_destination(tmp_path):
    source, destination = tmp_path / "source.mp4", tmp_path / "output.jpg"
    source.write_bytes(b"source")
    destination.write_bytes(b"sentinel")

    def run(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial")
        raise KeyboardInterrupt

    with (
        patch.object(
            conversion, "get_availability", return_value=availability("ffmpeg")
        ),
        patch.object(conversion.subprocess, "run", side_effect=run),
        pytest.raises(KeyboardInterrupt),
    ):
        conversion.convert_file(source, destination)
    assert destination.read_bytes() == b"sentinel"
    assert set(tmp_path.iterdir()) == {source, destination}
