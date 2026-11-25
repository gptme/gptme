"""A lone carriage return before a line ending must survive an edit elsewhere.

``read_text_lf`` normalizes CRLF to LF but keeps a lone CR inside a line as
content. When that lone CR is the last character of a line, the LF model
represents it as ``"\\r\\n"`` -- byte-identical to a CRLF line ending. Any
writer that normalizes CRLF again therefore deletes that CR from an untouched
line. These tests pin the bytes for every editing writer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gptme.tools.base import read_text_lf
from gptme.tools.hashline_edit import execute_hashline_edit, store_snapshot
from gptme.tools.morph import execute_morph_impl
from gptme.tools.patch import execute_patch
from gptme.tools.patch_many import execute_patch_many

if TYPE_CHECKING:
    from pathlib import Path

ORIGINAL_BYTES = b"a = 1\r\r\nb = 1\r\n"
EXPECTED_BYTES = b"a = 1\r\r\nb = 2\r\n"

PATCH_CODE = "<<<<<<< ORIGINAL\nb = 1\n=======\nb = 2\n>>>>>>> UPDATED"


def _messages(gen) -> list[str]:
    return [m.content for m in gen]


def test_read_text_lf_keeps_trailing_cr(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)
    assert read_text_lf(f) == "a = 1\r\nb = 1\n"


def test_patch_keeps_trailing_cr(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)

    msgs = _messages(execute_patch(PATCH_CODE, [str(f)], None))

    assert "successfully" in msgs[0].lower(), msgs
    assert f.read_bytes() == EXPECTED_BYTES


def test_patch_many_keeps_trailing_cr(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)

    msgs = _messages(execute_patch_many(PATCH_CODE, [str(f)], None))

    assert "atomically" in " ".join(msgs).lower(), msgs
    assert f.read_bytes() == EXPECTED_BYTES


def test_hashline_keeps_trailing_cr(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)
    snapshot = read_text_lf(f)
    tag = store_snapshot(str(f.resolve()), snapshot)
    block = f"[{f.resolve()}#{tag}]\nPUT 2.=2:\n+b = 2\n"

    msgs = _messages(execute_hashline_edit(block, [str(f)], None))

    assert "applied" in msgs[0].lower(), msgs
    assert f.read_bytes() == EXPECTED_BYTES


def test_morph_keeps_trailing_cr(tmp_path: Path):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)
    original = read_text_lf(f)

    _messages(execute_morph_impl("a = 1\r\nb = 2\n", f, original))

    assert f.read_bytes() == EXPECTED_BYTES
