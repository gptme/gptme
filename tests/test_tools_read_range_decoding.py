"""Explicit ranges must not depend on invalid bytes outside the selection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.tools.read import execute_read


@pytest.mark.parametrize("padding", [0, 1024 * 1024])
@pytest.mark.parametrize(
    ("data", "start", "expected", "invalid"),
    [
        (b"wanted\n\xff\n", 1, "wanted", False),
        (b"\xff\nwanted\n", 2, "wanted", False),
        (b"before\nwanted:\xff\n", 2, "wanted:�", True),
        ("before\r\nwanted: café\rafter\n".encode(), 2, "wanted: café", False),
    ],
)
def test_range_decoding_ignores_unselected_bytes(
    tmp_path: Path, padding: int, data: bytes, start: int, expected: str, invalid: bool
) -> None:
    path = tmp_path / "mixed.log"
    path.write_bytes(data + b"x" * padding)
    with patch("gptme.tools.notify_file_read") as notify:
        messages = list(
            execute_read(
                None,
                None,
                {"path": str(path), "start_line": str(start), "end_line": str(start)},
            )
        )
    content = messages[0].content
    assert expected in content
    assert "Cannot read binary file" not in content
    assert ("contains invalid UTF-8 bytes" in content) == invalid
    if b"\xff" in data or padding:
        # A selected view of damaged/large content is never a full exact snapshot.
        notify.assert_not_called()


def test_whole_file_still_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "mixed.log"
    path.write_bytes(b"wanted\n\xff\n")
    with patch("gptme.tools.notify_file_read") as notify:
        messages = list(execute_read(None, [str(path)], None))
    assert "Cannot read binary file" in messages[0].content
    notify.assert_not_called()
