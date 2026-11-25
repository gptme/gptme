"""A lone carriage return before a line ending must survive an edit elsewhere.

``read_text_lf`` normalizes CRLF to LF but keeps a lone CR inside a line as
content. When that lone CR is the last character of a line, the LF model
represents it as ``"\\r\\n"`` -- byte-identical to a CRLF line ending. Any
writer that normalizes CRLF again therefore deletes that CR from an untouched
line. These tests pin the bytes for every editing writer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gptme.hooks import ConfirmationResult
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


@pytest.mark.parametrize(
    ("original", "response", "expected"),
    [
        # interior lone CR, provider answers with CRLF
        (b"a\rb\r\nold\r\n", "a\rb\r\nnew\r\n", b"a\rb\r\nnew\r\n"),
        # CR before the line ending, provider answers with CRLF and keeps it
        (ORIGINAL_BYTES, "a = 1\r\r\nb = 2\r\n", EXPECTED_BYTES),
        # CR before the line ending, provider collapses it into its CRLF
        (ORIGINAL_BYTES, "a = 1\r\nb = 2\r\n", EXPECTED_BYTES),
        # CR before the line ending, provider answers with LF and drops it
        (ORIGINAL_BYTES, "a = 1\nb = 2\n", EXPECTED_BYTES),
        # a changed line keeps the CR when the answer is not CRLF-encoded
        (ORIGINAL_BYTES, "a = 2\r\nb = 1\n", b"a = 2\r\r\nb = 1\r\n"),
        # the CR after the last newline is content: it can be added or removed
        (b"a\r\r\nlast", "a\r\nlast\r", b"a\r\r\nlast\r"),
        (b"a\r\r\nlast\r", "a\r\nlast", b"a\r\r\nlast"),
        # inserted lines get no extra CR, including at the end of the file
        (
            ORIGINAL_BYTES,
            "new\r\na = 1\r\r\nb = 1\r\nc = 3\r\n",
            b"new\r\na = 1\r\r\nb = 1\r\nc = 3\r\n",
        ),
    ],
)
def test_morph_strips_only_provider_crs(
    tmp_path: Path, original: bytes, response: str, expected: bytes
):
    f = tmp_path / "f.txt"
    f.write_bytes(original)

    _messages(execute_morph_impl(response, f, read_text_lf(f)))

    assert f.read_bytes() == expected


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        # the line with the CR is repeated as context
        (
            "<<<<<<< ORIGINAL\na = 1\r\nb = 1\n=======\na = 1\r\nb = 2\n>>>>>>> UPDATED",
            EXPECTED_BYTES,
        ),
        # the chunk ends at the CR and the replacement adds a line after it
        (
            "<<<<<<< ORIGINAL\na = 1\r\n=======\na = 1\r\nc = 3\n>>>>>>> UPDATED",
            b"a = 1\r\r\nc = 3\r\nb = 1\r\n",
        ),
    ],
)
def test_patch_keeps_trailing_cr_in_context(
    tmp_path: Path, patch: str, expected: bytes
):
    f = tmp_path / "f.txt"
    f.write_bytes(ORIGINAL_BYTES)

    msgs = _messages(execute_patch(patch, [str(f)], None))

    assert "successfully" in msgs[0].lower(), msgs
    assert f.read_bytes() == expected


@pytest.mark.parametrize(
    ("original", "edited", "expected"),
    [
        # CRLF file, the editor returns CRLF line endings
        (b"a = 1\r\nb = 1\r\n", "a = 1\r\nb = 3\r\n", b"a = 1\r\nb = 3\r\n"),
        # LF file, same editor
        (b"a = 1\nb = 1\n", "a = 1\r\nb = 3\r\n", b"a = 1\nb = 3\n"),
        # a preserved CR survives both editor styles
        (ORIGINAL_BYTES, "a = 1\r\r\nb = 3\r\n", b"a = 1\r\r\nb = 3\r\n"),
        (ORIGINAL_BYTES, "a = 1\r\nb = 3\n", b"a = 1\r\r\nb = 3\r\n"),
    ],
)
def test_hashline_normalizes_edited_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original: bytes,
    edited: str,
    expected: bytes,
):
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation",
        lambda **_: ConfirmationResult.edit(edited),
    )
    f = tmp_path / "f.txt"
    f.write_bytes(original)
    tag = store_snapshot(str(f.resolve()), read_text_lf(f))
    block = f"[{f.resolve()}#{tag}]\nPUT 2.=2:\n+b = 2\n"

    msgs = _messages(execute_hashline_edit(block, [str(f)], None))

    assert "applied" in msgs[0].lower(), msgs
    assert f.read_bytes() == expected
