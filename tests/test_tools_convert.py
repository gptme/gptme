"""Tests for gptme.tools.convert."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.tools.convert import (
    ConversionResult,
    DocumentToTextConverter,
    ImageConverter,
    PDFToImageConverter,
    PDFToTextConverter,
    ToolAvailability,
    VideoThumbnailConverter,
    _arg,
    convert_file,
    find_converter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def avail_all():
    """ToolAvailability with everything available."""
    return ToolAvailability(
        ffmpeg=True,
        imagemagick=True,
        pdftoppm=True,
        pdftotext=True,
        libreoffice=True,
        tesseract=True,
        python_magic=False,  # tested separately
        python_docx=True,
        pypdf=True,
    )


@pytest.fixture
def avail_none():
    """ToolAvailability with nothing available."""
    return ToolAvailability(
        ffmpeg=False,
        imagemagick=False,
        pdftoppm=False,
        pdftotext=False,
        libreoffice=False,
        tesseract=False,
        python_magic=False,
        python_docx=False,
        pypdf=False,
    )


@pytest.fixture
def avail_ffmpeg_only():
    return ToolAvailability(
        ffmpeg=True,
        imagemagick=False,
        pdftoppm=False,
        pdftotext=False,
        libreoffice=False,
        tesseract=False,
        python_magic=False,
        python_docx=False,
        pypdf=False,
    )


@pytest.fixture
def avail_imagemagick_only():
    return ToolAvailability(
        ffmpeg=False,
        imagemagick=True,
        pdftoppm=False,
        pdftotext=False,
        libreoffice=False,
        tesseract=False,
        python_magic=False,
        python_docx=False,
        pypdf=False,
    )


# ---------------------------------------------------------------------------
# ToolAvailability tests
# ---------------------------------------------------------------------------


def test_tool_availability_report_contains_marks():
    avail = ToolAvailability(ffmpeg=True, imagemagick=False)
    report = avail.report()
    assert "✓" in report
    assert "⚠" in report


def test_tool_availability_defaults_are_bool():
    avail = ToolAvailability()
    assert isinstance(avail.ffmpeg, bool)
    assert isinstance(avail.imagemagick, bool)


# ---------------------------------------------------------------------------
# PDFToImageConverter
# ---------------------------------------------------------------------------


class TestPDFToImageConverter:
    conv = PDFToImageConverter()

    def test_is_available_with_pdftoppm(self, avail_all):
        assert self.conv.is_available(avail_all)

    def test_is_available_with_imagemagick(self, avail_imagemagick_only):
        assert self.conv.is_available(avail_imagemagick_only)

    def test_not_available_when_nothing(self, avail_none):
        assert not self.conv.is_available(avail_none)

    def test_can_handle_pdf_to_png(self):
        assert self.conv.can_handle("application/pdf", "png")

    def test_can_handle_pdf_to_jpg(self):
        assert self.conv.can_handle("application/pdf", "jpg")

    def test_cannot_handle_image_to_png(self):
        assert not self.conv.can_handle("image/png", "png")

    def test_error_result_when_no_tools(self, avail_none, tmp_path):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")  # minimal fake PDF
        dest = tmp_path / "out.png"
        with patch("gptme.tools.convert.get_availability", return_value=avail_none):
            result = self.conv.convert(src, dest)
        assert not result.success
        assert result.error is not None
        assert "converter" in result.error.lower()

    def test_uses_pdftoppm_when_available(self, avail_all, tmp_path):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        dest = tmp_path / "out.png"

        def fake_run(cmd, **kwargs):
            if "pdftoppm" in cmd:
                # Create fake output file
                out_prefix = Path(cmd[-1])
                (out_prefix.parent / f"{out_prefix.name}-1.png").write_bytes(b"\x89PNG")
                return MagicMock(returncode=0, stderr=b"")
            return MagicMock(returncode=1, stderr=b"fail")

        with (
            patch("gptme.tools.convert.get_availability", return_value=avail_all),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = self.conv.convert(src, dest)
        assert result.success
        assert result.converter_used == "pdftoppm"

    def test_falls_back_to_imagemagick_on_pdftoppm_failure(self, avail_all, tmp_path):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        dest = tmp_path / "out.png"
        dest.write_bytes(b"\x89PNG")  # pre-create so rename works

        call_count = {"n": 0}

        def fake_run(cmd, **kwargs):
            call_count["n"] += 1
            if "pdftoppm" in cmd:
                return MagicMock(returncode=1, stderr=b"pdftoppm error")
            # imagemagick
            dest.write_bytes(b"\x89PNG")
            return MagicMock(returncode=0, stderr=b"")

        with (
            patch("gptme.tools.convert.get_availability", return_value=avail_all),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = self.conv.convert(src, dest)
        assert result.success
        assert result.converter_used == "imagemagick"
        assert call_count["n"] == 2

    def test_rejects_existing_directory_as_dest(self, avail_all, tmp_path):
        """shutil.move silently moves the file INTO a directory; guard must catch this."""
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        dest_dir = tmp_path / "output_dir"
        dest_dir.mkdir()
        with patch("gptme.tools.convert.get_availability", return_value=avail_all):
            result = self.conv.convert(src, dest_dir)
        assert not result.success
        assert result.error is not None
        assert "directory" in result.error.lower()


# ---------------------------------------------------------------------------
# ImageConverter
# ---------------------------------------------------------------------------


class TestImageConverter:
    conv = ImageConverter()

    def test_can_handle_image_to_webp(self):
        assert self.conv.can_handle("image/png", "webp")

    def test_can_handle_image_to_jpg(self):
        assert self.conv.can_handle("image/jpeg", "jpg")

    def test_cannot_handle_pdf(self):
        assert not self.conv.can_handle("application/pdf", "png")

    def test_not_available_when_nothing(self, avail_none):
        assert not self.conv.is_available(avail_none)

    def test_is_available_with_ffmpeg(self, avail_ffmpeg_only):
        assert self.conv.is_available(avail_ffmpeg_only)

    def test_is_available_with_imagemagick(self, avail_imagemagick_only):
        assert self.conv.is_available(avail_imagemagick_only)

    def test_ffmpeg_success(self, avail_ffmpeg_only, tmp_path):
        src = tmp_path / "img.png"
        src.write_bytes(b"\x89PNG")
        dest = tmp_path / "out.webp"

        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"RIFF")
            return MagicMock(returncode=0, stderr=b"")

        with (
            patch(
                "gptme.tools.convert.get_availability", return_value=avail_ffmpeg_only
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = self.conv.convert(src, dest)
        assert result.success
        assert result.converter_used == "ffmpeg"
        assert result.lossy  # webp is lossy by default

    def test_png_is_not_lossy(self, avail_ffmpeg_only, tmp_path):
        src = tmp_path / "img.jpg"
        src.write_bytes(b"\xff\xd8\xff")
        dest = tmp_path / "out.png"

        def fake_run(cmd, **kwargs):
            dest.write_bytes(b"\x89PNG")
            return MagicMock(returncode=0, stderr=b"")

        with (
            patch(
                "gptme.tools.convert.get_availability", return_value=avail_ffmpeg_only
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = self.conv.convert(src, dest)
        assert result.success
        assert not result.lossy


# ---------------------------------------------------------------------------
# PDFToTextConverter
# ---------------------------------------------------------------------------


class TestPDFToTextConverter:
    conv = PDFToTextConverter()

    def test_can_handle_pdf_to_txt(self):
        assert self.conv.can_handle("application/pdf", "txt")

    def test_cannot_handle_pdf_to_png(self):
        assert not self.conv.can_handle("application/pdf", "png")

    def test_error_when_nothing_available(self, avail_none, tmp_path):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        dest = tmp_path / "out.txt"
        with patch("gptme.tools.convert.get_availability", return_value=avail_none):
            result = self.conv.convert(src, dest)
        assert not result.success

    def test_rejects_existing_directory_as_dest(self, avail_all, tmp_path):
        src = tmp_path / "doc.pdf"
        src.write_bytes(b"%PDF-1.4")
        dest = tmp_path / "output_dir"
        dest.mkdir()

        with patch("gptme.tools.convert.get_availability", return_value=avail_all):
            result = self.conv.convert(src, dest)

        assert not result.success
        assert result.output_path == dest
        assert result.error is not None
        assert "directory" in result.error.lower()


# ---------------------------------------------------------------------------
# DocumentToTextConverter
# ---------------------------------------------------------------------------


class TestDocumentToTextConverter:
    conv = DocumentToTextConverter()

    def test_can_handle_docx_to_txt(self):
        assert self.conv.can_handle(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt",
        )

    def test_cannot_handle_pdf(self):
        assert not self.conv.can_handle("application/pdf", "txt")

    def test_rejects_existing_directory_as_dest(self, avail_all, tmp_path):
        src = tmp_path / "doc.docx"
        src.write_bytes(b"PK")
        dest = tmp_path / "output_dir"
        dest.mkdir()

        with patch("gptme.tools.convert.get_availability", return_value=avail_all):
            result = self.conv.convert(src, dest)

        assert not result.success
        assert result.output_path == dest
        assert result.error is not None
        assert "directory" in result.error.lower()


# ---------------------------------------------------------------------------
# VideoThumbnailConverter
# ---------------------------------------------------------------------------


class TestVideoThumbnailConverter:
    conv = VideoThumbnailConverter()

    def test_can_handle_mp4_to_jpg(self):
        assert self.conv.can_handle("video/mp4", "jpg")

    def test_cannot_handle_image(self):
        assert not self.conv.can_handle("image/png", "jpg")

    def test_error_without_ffmpeg(self, avail_none, tmp_path):
        src = tmp_path / "vid.mp4"
        src.write_bytes(b"\x00\x00\x00\x20ftyp")
        dest = tmp_path / "thumb.jpg"
        with patch("gptme.tools.convert.get_availability", return_value=avail_none):
            result = self.conv.convert(src, dest)
        assert not result.success
        assert result.error is not None
        assert "FFmpeg" in result.error


# ---------------------------------------------------------------------------
# find_converter / convert_file (integration-style)
# ---------------------------------------------------------------------------


def test_find_converter_pdf_to_png(avail_all, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4")
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("gptme.tools.convert._detect_mime", return_value="application/pdf"),
    ):
        conv = find_converter(src, "png", avail_all)
    assert conv is not None
    assert isinstance(conv, PDFToImageConverter)


def test_find_converter_image_to_webp(avail_ffmpeg_only, tmp_path):
    src = tmp_path / "img.png"
    src.write_bytes(b"\x89PNG")
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_ffmpeg_only),
        patch("gptme.tools.convert._detect_mime", return_value="image/png"),
    ):
        conv = find_converter(src, "webp", avail_ffmpeg_only)
    assert conv is not None
    assert isinstance(conv, ImageConverter)


def test_find_converter_returns_none_for_unknown(avail_none, tmp_path):
    src = tmp_path / "mystery.xyz"
    src.write_bytes(b"garbage")
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_none),
        patch(
            "gptme.tools.convert._detect_mime", return_value="application/octet-stream"
        ),
    ):
        conv = find_converter(src, "png", avail_none)
    assert conv is None


def test_convert_file_dry_run(avail_all, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "out.png"
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("gptme.tools.convert._detect_mime", return_value="application/pdf"),
    ):
        result = convert_file(src, dest, dry_run=True)
    assert result.success
    assert result.metadata.get("dry_run") is True
    assert not dest.exists()  # dry_run should NOT create the file


def test_convert_file_no_converter(avail_none, tmp_path):
    src = tmp_path / "mystery.xyz"
    src.write_bytes(b"garbage")
    dest = tmp_path / "out.png"
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_none),
        patch(
            "gptme.tools.convert._detect_mime", return_value="application/octet-stream"
        ),
    ):
        result = convert_file(src, dest)
    assert not result.success
    assert result.error


# ---------------------------------------------------------------------------
# ConversionResult.summary()
# ---------------------------------------------------------------------------


def test_conversion_result_summary_success():
    result = ConversionResult(
        success=True,
        output_path=Path("/tmp/out.png"),
        converter_used="pdftoppm",
        warnings=["multi-page PDF"],
    )
    summary = result.summary()
    assert "pdftoppm" in summary
    assert "multi-page" in summary


def test_conversion_result_summary_failure():
    result = ConversionResult(
        success=False,
        output_path=None,
        converter_used="ffmpeg",
        error="Exit code 1",
    )
    summary = result.summary()
    assert "failed" in summary.lower()
    assert "Exit code 1" in summary


def test_convert_file_same_path_rejected(avail_all, tmp_path):
    """convert_file must reject src == dest to prevent data corruption."""
    src = tmp_path / "photo.png"
    src.write_bytes(b"\x89PNG")
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("gptme.tools.convert._detect_mime", return_value="image/png"),
    ):
        result = convert_file(src, src)
    assert not result.success
    assert result.error is not None
    assert "different" in result.error.lower()


def test_pdftoppm_failure_cleans_partial_artifacts(avail_all, tmp_path):
    """Partial page files from a failed pdftoppm run must be removed before ImageMagick fallback."""
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "out.png"

    partial_artifact: Path | None = None

    def fake_run(cmd, **kwargs):
        nonlocal partial_artifact
        mock = MagicMock()
        if "pdftoppm" in cmd:
            # Simulate pdftoppm writing a partial page file into the tmpdir before failing
            # cmd[-1] is the output prefix (inside a TemporaryDirectory); artifact lives there
            tmp_prefix = Path(cmd[-1])
            partial_artifact = tmp_prefix.parent / f"{tmp_prefix.name}-1.png"
            partial_artifact.write_bytes(b"partial")
            assert partial_artifact.exists()
            mock.returncode = 1
            mock.stderr = b"pdftoppm error"
        else:
            mock.returncode = 1
            mock.stderr = b"imagemagick error"
        return mock

    conv = PDFToImageConverter()
    avail_pdftoppm_only = ToolAvailability(
        pdftoppm=True,
        imagemagick=False,
        ffmpeg=False,
        pdftotext=False,
        libreoffice=False,
        tesseract=False,
        python_magic=False,
        python_docx=False,
        pypdf=False,
    )
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_pdftoppm_only),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = conv.convert(src, dest)

    assert partial_artifact is not None
    assert not partial_artifact.exists(), (
        "Temporary pdftoppm artifact was not cleaned up"
    )
    assert not result.success


def test_can_handle_uppercase_extension(avail_all):
    """can_handle must accept uppercase extensions like .JPG and .PNG."""
    conv = ImageConverter()
    assert conv.can_handle("image/png", "JPG")
    assert conv.can_handle("image/jpeg", "PNG")
    assert conv.can_handle("image/png", "WEBP")


def test_convert_file_no_converter_error_message_is_correct(avail_none, tmp_path):
    """Error message must name the real CLI subcommand, not a non-existent flag."""
    src = tmp_path / "photo.heic"
    src.write_bytes(b"HEIF")
    dest = tmp_path / "out.png"
    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_none),
        patch("gptme.tools.convert._detect_mime", return_value="image/heic"),
    ):
        result = convert_file(src, dest)
    assert not result.success
    assert result.error is not None
    # Must not reference the non-existent --check-tools flag
    assert "--check-tools" not in result.error
    assert "check-tools" in result.error


def test_ffmpeg_webp_uses_quality_not_qv(avail_ffmpeg_only, tmp_path):
    """For WebP, ffmpeg must receive -quality (0-100) not -q:v (inverted JPEG scale)."""
    src = tmp_path / "img.png"
    src.write_bytes(b"\x89PNG")
    dest = tmp_path / "out.webp"

    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        dest.write_bytes(b"RIFF")
        return MagicMock(returncode=0, stderr=b"")

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_ffmpeg_only),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = ImageConverter().convert(src, dest, quality="high")

    assert result.success
    assert "-quality" in captured_cmd
    assert "-q:v" not in captured_cmd
    # FFmpeg supports -- as an option terminator; keep it so dash-prefixed paths are safe.
    assert captured_cmd[-2:] == ["--", str(dest)]


# ---------------------------------------------------------------------------
# Option-shaped paths (argument injection)
# ---------------------------------------------------------------------------


def test_arg_prefixes_dash_leading_paths():
    """A path starting with '-' must not reach a subprocess as an option."""
    assert _arg(Path("-o")) == "./-o"
    assert _arg(Path("-rf")) == "./-rf"
    # Normal paths pass through untouched
    assert _arg(Path("out.png")) == "out.png"
    assert _arg(Path("/tmp/out.png")) == "/tmp/out.png"
    assert _arg(Path("dir/-o.png")) == "dir/-o.png"


def test_pdftotext_protects_dash_leading_source(avail_all, tmp_path, monkeypatch):
    """pdftotext has no `--` terminator, so the path itself must be de-optioned."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "-o"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "out.txt"
    avail_all.pypdf = False

    captured: list[str] = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return MagicMock(returncode=0, stderr=b"")

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("subprocess.run", side_effect=fake_run),
    ):
        result = PDFToTextConverter().convert(Path("-o"), dest)

    assert result.success
    assert "-o" not in captured
    assert "./-o" in captured


def test_pdftoppm_protects_dash_leading_source(avail_all, tmp_path, monkeypatch):
    """pdftoppm parses a leading-dash filename as a flag unless de-optioned."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "-r"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "out.png"

    captured: list[str] = []

    def fake_run(cmd, **kwargs):
        captured.extend(cmd)
        return MagicMock(returncode=1, stderr=b"boom")

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("subprocess.run", side_effect=fake_run),
    ):
        PDFToImageConverter().convert(Path("-r"), dest)

    # "-r" appears as the dpi flag, but never as the source argument
    assert captured.count("-r") == 1
    assert "./-r" in captured


# ---------------------------------------------------------------------------
# Directory destinations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "converter", [ImageConverter, VideoThumbnailConverter], ids=["image", "video"]
)
def test_directory_destination_rejected(converter, avail_ffmpeg_only, tmp_path):
    """Every converter must reject a directory destination, not hand it to a subprocess."""
    src = tmp_path / "in.png"
    src.write_bytes(b"\x89PNG")
    dest = tmp_path / "outdir"
    dest.mkdir()

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_ffmpeg_only),
        patch("subprocess.run") as run,
    ):
        result = converter().convert(src, dest)

    assert not result.success
    assert "existing directory" in (result.error or "")
    run.assert_not_called()


# ---------------------------------------------------------------------------
# Unwritable destinations return a result, never a traceback
# ---------------------------------------------------------------------------


def test_pdf_to_text_unwritable_dest_returns_failure(avail_all, tmp_path):
    """An OSError on write must become ConversionResult(success=False), not crash."""
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "out.txt"

    reader = MagicMock()
    page = MagicMock()
    page.extract_text.return_value = "hello"
    reader.pages = [page]
    fake_pypdf = MagicMock(PdfReader=MagicMock(return_value=reader))

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch.dict(sys.modules, {"pypdf": fake_pypdf}),
        patch.object(Path, "write_text", side_effect=OSError("Read-only file system")),
    ):
        result = PDFToTextConverter().convert(src, dest)

    assert not result.success
    assert "Read-only file system" in (result.error or "")


def test_convert_file_unwritable_parent_returns_failure(avail_all, tmp_path):
    """mkdir failure on the output directory is a recoverable conversion error."""
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4")
    dest = tmp_path / "nested" / "out.png"

    with (
        patch("gptme.tools.convert.get_availability", return_value=avail_all),
        patch("gptme.tools.convert._detect_mime", return_value="application/pdf"),
        patch.object(Path, "mkdir", side_effect=OSError("Permission denied")),
    ):
        result = convert_file(src, dest)

    assert not result.success
    assert "Permission denied" in (result.error or "")


def test_module_docstring_does_not_claim_ocr():
    """The module advertised OCR via Tesseract, but no converter implements it."""
    import gptme.tools.convert as convert_mod

    assert convert_mod.__doc__ is not None
    assert "OCR" not in convert_mod.__doc__
