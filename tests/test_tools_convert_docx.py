"""Real DOCX fixtures for document text extraction (optional python-docx)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

docx = pytest.importorskip("docx")

from gptme.tools.convert import DocumentToTextConverter, ToolAvailability

if TYPE_CHECKING:
    from pathlib import Path

    from docx.document import Document


def convert(document: Document, tmp_path: Path, extension: str) -> str:
    source = tmp_path / "document.docx"
    target = tmp_path / f"document.{extension}"
    document.save(str(source))
    with patch(
        "gptme.tools.convert.get_availability",
        return_value=ToolAvailability(python_docx=True, libreoffice=False),
    ):
        result = DocumentToTextConverter().convert(source, target)
    assert result.success, result.error
    assert result.converter_used == "python-docx"
    return target.read_text()


@pytest.mark.parametrize("extension", ["txt", "md"])
@pytest.mark.parametrize("interleaved", [False, True])
def test_docx_table_text_in_body_order(
    tmp_path: Path, extension: str, interleaved: bool
) -> None:
    document = docx.Document()
    if interleaved:
        document.add_paragraph("BEFORE_TABLE")
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [("ITEM", "AMOUNT"), ("INVOICE", "123.45")]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    if interleaved:
        document.add_paragraph("AFTER_TABLE")
    text = convert(document, tmp_path, extension)
    expected = (
        "ITEM\tAMOUNT\nINVOICE\t123.45"
        if extension == "txt"
        else "| ITEM | AMOUNT |\n| --- | --- |\n| INVOICE | 123.45 |"
    )
    if interleaved:
        separator = "\n\n" if extension == "md" else "\n"
        expected = separator.join(["BEFORE_TABLE", expected, "AFTER_TABLE"])
    assert text == expected


@pytest.mark.parametrize("extension", ["txt", "md"])
def test_docx_paragraph_control(tmp_path: Path, extension: str) -> None:
    document = docx.Document()
    document.add_heading("Introduction", level=1)
    document.add_paragraph("Body text.")
    assert convert(document, tmp_path, extension) == (
        "# Introduction\nBody text."
        if extension == "md"
        else "Introduction\nBody text."
    )


def test_docx_markdown_cell_escaping_and_merged_cells(tmp_path: Path) -> None:
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged"
    table.cell(1, 0).text = "pipe|slash\\\nnext <tag>"
    table.cell(1, 1).text = ""
    assert convert(document, tmp_path, "md") == (
        "| Merged | Merged |\n| --- | --- |\n"
        "| pipe\\|slash\\\\<br>next &lt;tag&gt; |  |"
    )


def test_docx_text_multiline_cell(tmp_path: Path) -> None:
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "first\nsecond"
    table.cell(0, 1).text = "other"
    assert convert(document, tmp_path, "txt") == "first\nsecond\tother"


@pytest.mark.parametrize("extension", ["txt", "md"])
def test_docx_consecutive_tables(tmp_path: Path, extension: str) -> None:
    document = docx.Document()
    document.add_table(rows=1, cols=1).cell(0, 0).text = "First"
    document.add_table(rows=1, cols=1).cell(0, 0).text = "Second"
    document.add_paragraph("After")
    assert convert(document, tmp_path, extension) == (
        "| First |\n| --- |\n\n| Second |\n| --- |\n\nAfter"
        if extension == "md"
        else "First\nSecond\nAfter"
    )


@pytest.mark.parametrize("extension", ["txt", "md"])
def test_docx_vertical_merge_repeats_grid_text(tmp_path: Path, extension: str) -> None:
    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).merge(table.cell(1, 0)).text = "Shared"
    table.cell(0, 1).text = "A"
    table.cell(1, 1).text = "B"
    assert convert(document, tmp_path, extension) == (
        "| Shared | A |\n| --- | --- |\n| Shared | B |"
        if extension == "md"
        else "Shared\tA\nShared\tB"
    )
