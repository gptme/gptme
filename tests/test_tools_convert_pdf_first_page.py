"""Real Poppler regressions for bounded first-page PDF rasterization (#4668)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import gptme.tools.convert as convert_module
from gptme.tools.convert import ToolAvailability, convert_file


def _write_pdf(path: Path, pages: int) -> bytes:
    """Write a tiny valid PDF with distinct vector-only pages, using only stdlib."""
    page_ids = [3 + 2 * index for index in range(pages)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
    ]
    for index, page_id in enumerate(page_ids):
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 48] "
                f"/Resources << >> /Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        # Different rectangle widths make every page visibly distinct at all DPIs.
        stream = f"0.2 0.4 0.8 rg 4 4 {index + 5} 40 re f\n".encode()
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream"
        )

    data = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        (
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(data)
    return bytes(data)


@pytest.fixture
def pdftoppm_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use only real pdftoppm, without metadata libraries or other converters."""
    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm is not installed on PATH")
    monkeypatch.setattr(
        convert_module,
        "_tool_avail",
        ToolAvailability(
            ffmpeg=False,
            imagemagick=False,
            pdftoppm=True,
            pdftotext=False,
            libreoffice=False,
            tesseract=False,
            python_magic=False,
            python_docx=False,
            pypdf=False,
        ),
    )


@pytest.mark.parametrize("pages", [1, 12])
@pytest.mark.parametrize(
    ("quality", "dpi"), [("low", 72), ("medium", 150), ("high", 300)]
)
@pytest.mark.parametrize("extension", ["png", "jpg", "jpeg"])
@pytest.mark.parametrize(
    "leading_option", [False, True], ids=["literal", "option-shaped"]
)
def test_pdf_rasterizes_only_first_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pdftoppm_only: None,
    pages: int,
    quality: str,
    dpi: int,
    extension: str,
    leading_option: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    # Both cases exercise literal spaces/brackets; the second also requires ./
    # protection for relative paths that would otherwise look like CLI options.
    prefix = "-" if leading_option else ""
    src = Path(f"{prefix}source [0] document.pdf")
    dest = Path(f"{prefix}output [1] image.{extension}")
    original = _write_pdf(src, pages)

    control_dir = tmp_path / "control"
    control_dir.mkdir()
    format_flag = "-png" if extension == "png" else "-jpeg"
    control = subprocess.run(
        [
            "pdftoppm",
            format_flag,
            "-r",
            str(dpi),
            "-f",
            "1",
            "-l",
            "1",
            str(src.resolve()),
            str(control_dir / "first"),
        ],
        capture_output=True,
        check=False,
    )
    assert control.returncode == 0, control.stderr
    control_files = list(control_dir.iterdir())
    assert len(control_files) == 1
    expected = control_files[0].read_bytes()

    real_run = subprocess.run
    generated: list[list[bytes]] = []

    def observe_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        # Reject extra metadata subprocesses, but execute the actual renderer.
        assert cmd[0] == "pdftoppm", f"Unexpected subprocess: {cmd}"
        completed = real_run(cmd, **kwargs)
        # Inspect before the converter moves a page and deletes its tempdir.
        output_dir = Path(cmd[-1]).parent
        generated.append(
            [
                image.read_bytes()
                for image in sorted(output_dir.iterdir())
                if image.suffix.lower() in {".png", ".jpg", ".jpeg"}
            ]
        )
        return completed

    monkeypatch.setattr(convert_module.subprocess, "run", observe_run)
    result = convert_file(src, dest, quality=quality)

    assert src.read_bytes() == original
    assert result.success, result.error
    assert result.converter_used == "pdftoppm"
    assert result.output_path == dest
    assert result.lossy is (extension != "png")
    assert dest.read_bytes() == expected
    assert len(generated) == 1, "Expected exactly one renderer subprocess"
    assert len(generated[0]) == 1, "Renderer must not rasterize discarded pages"
    assert generated[0][0] == expected
    warning = " ".join(result.warnings).lower()
    assert "pdf-to-image conversion saves only the first page" in warning
    assert "total" not in warning, "No page-count metadata was queried"
    assert "multi-page" not in warning, "Do not guess the source page count"
    assert set(tmp_path.iterdir()) == {src.resolve(), dest.resolve(), control_dir}
