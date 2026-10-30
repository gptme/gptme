"""Reject special sources before content inspection or backend admission."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from gptme.tools import convert

if TYPE_CHECKING:
    from pathlib import Path


def symlink_to(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError:
        if sys.platform == "win32":
            pytest.skip("symlink creation requires Windows privileges")
        raise


@pytest.fixture(
    params=["fifo", "fifo-symlink", "directory", "missing", "dangling-symlink"]
)
def invalid_source(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    src = tmp_path / "input.docx"
    if request.param in ("fifo", "fifo-symlink"):
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFOs unavailable on this platform")
        fifo = tmp_path / "pipe.docx"
        os.mkfifo(fifo)
        if request.param == "fifo":
            return fifo
        symlink_to(src, fifo)
    elif request.param == "directory":
        src.mkdir()
    elif request.param == "dangling-symlink":
        symlink_to(src, tmp_path / "absent.docx")
    return src


def forbid_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid source reached MIME inspection or backend admission")

    monkeypatch.setattr(convert, "_detect_mime", unexpected)
    monkeypatch.setattr(convert, "get_availability", unexpected)
    monkeypatch.setattr(convert.subprocess, "run", unexpected)


@pytest.mark.parametrize("dry_run", [False, True])
def test_router_rejects_invalid_source_before_inspection(
    invalid_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    forbid_admission(monkeypatch)
    dest = tmp_path / "uncreated" / "out.txt"
    result = convert.convert_file(invalid_source, dest, dry_run=dry_run)
    assert not result.success
    assert result.error and "input" in result.error.lower()
    assert not dest.parent.exists()


def test_find_converter_rejects_invalid_source_before_inspection(
    invalid_source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbid_admission(monkeypatch)
    with pytest.raises(ValueError, match="[Ii]nput"):
        convert.find_converter(invalid_source, "txt")


@pytest.mark.parametrize(
    "converter",
    [
        convert.PDFToImageConverter,
        convert.PDFToTextConverter,
        convert.ImageConverter,
        convert.DocumentToTextConverter,
        convert.VideoThumbnailConverter,
    ],
)
def test_concrete_converter_rejects_invalid_source_before_backend(
    invalid_source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    converter: type[convert.Converter],
) -> None:
    forbid_admission(monkeypatch)
    dest = tmp_path / "out.txt"
    dest.write_bytes(b"valuable existing output")
    result = converter().convert(invalid_source, dest)
    assert not result.success
    assert result.error and "input" in result.error.lower()
    assert dest.read_bytes() == b"valuable existing output"


@pytest.mark.parametrize("dry_run", [False, True])
def test_structured_rejects_invalid_source(
    invalid_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    forbid_admission(monkeypatch)
    dest = tmp_path / "out.txt"
    result = convert._execute_convert(
        None,
        None,
        {
            "input_path": str(invalid_source),
            "output_path": str(dest),
            "dry_run": str(dry_run),
        },
    )
    assert "input" in result.content.lower()
    assert "error" in result.content.lower() or "failed" in result.content.lower()
    assert not dest.exists()


@pytest.mark.parametrize("dry_run", [False, True])
def test_cli_rejects_invalid_source(
    invalid_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    forbid_admission(monkeypatch)
    dest = tmp_path / "out.txt"
    args = ["convert", "--input", str(invalid_source), "--output", str(dest)]
    if dry_run:
        args.append("--dry-run")
    assert convert.main(args) == 1
    assert not dest.exists()


@pytest.mark.parametrize("symlink", [False, True])
@pytest.mark.parametrize("entry", ["router", "concrete", "structured", "cli"])
def test_real_docx_regular_source_control(
    tmp_path: Path, symlink: bool, entry: str
) -> None:
    docx = pytest.importorskip("docx")
    src = tmp_path / "input.docx"
    document = docx.Document()
    document.add_paragraph("ordinary source still converts")
    document.save(src)
    original = src.read_bytes()
    if symlink:
        link = tmp_path / "link.docx"
        symlink_to(link, src)
        src = link
    dest = tmp_path / "out.txt"
    if entry == "router":
        plan = convert.convert_file(src, dest, dry_run=True)
        assert plan.success and not dest.exists()
        assert convert.convert_file(src, dest).success
    elif entry == "concrete":
        assert convert.DocumentToTextConverter().convert(src, dest).success
    elif entry == "structured":
        result = convert._execute_convert(
            None, None, {"input_path": str(src), "output_path": str(dest)}
        )
        assert "failed" not in result.content.lower()
    else:
        assert (
            convert.main(["convert", "--input", str(src), "--output", str(dest)]) == 0
        )
    assert dest.read_text() == "ordinary source still converts"
    assert src.read_bytes() == original


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs unavailable")
@pytest.mark.parametrize("symlink", [False, True])
def test_fifo_real_entrypoints_finish_without_writer(
    tmp_path: Path, symlink: bool
) -> None:
    """Bound the actual regression: no mocked probe or backend can hide a hang."""
    src = tmp_path / "input.docx"
    os.mkfifo(src)
    if symlink:
        link = tmp_path / "link.docx"
        symlink_to(link, src)
        src = link
    dest = tmp_path / "out.txt"
    code = """
import sys
from pathlib import Path
from gptme.tools import convert
src, dest = map(Path, sys.argv[1:])
for dry_run in (False, True):
    result = convert.convert_file(src, dest, dry_run=dry_run)
    assert not result.success and 'not a regular file' in result.error
    result = convert._execute_convert(None, None, {
        'input_path': str(src), 'output_path': str(dest), 'dry_run': str(dry_run)})
    assert 'not a regular file' in result.content
    args = ['convert', '--input', str(src), '--output', str(dest)]
    assert convert.main(args + (['--dry-run'] if dry_run else [])) == 1
for cls in (convert.PDFToImageConverter, convert.PDFToTextConverter,
            convert.DocumentToTextConverter, convert.VideoThumbnailConverter,
            convert.ImageConverter):
    result = cls().convert(src, dest)
    assert not result.success and 'not a regular file' in result.error
assert not dest.exists()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(src), str(dest)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not dest.exists()


def test_source_inspection_error_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "input.docx"
    src.write_bytes(b"source")
    original_stat = type(src).stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == src:
            raise PermissionError("inspection denied")
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(type(src), "stat", denied)
    forbid_admission(monkeypatch)
    result = convert.convert_file(src, tmp_path / "out.txt")
    assert not result.success
    assert result.error and "Could not inspect input file" in result.error
    assert "inspection denied" in result.error
