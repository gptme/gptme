"""Tests for the read tool."""

from pathlib import Path

from gptme.tools.read import execute_read


def test_read_file(tmp_path: Path):
    """Test reading an entire file."""
    path = tmp_path / "test.txt"
    path.write_text("line 1\nline 2\nline 3\n")

    messages = list(execute_read(None, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "line 1" in messages[0].content
    assert "line 2" in messages[0].content
    assert "line 3" in messages[0].content


def test_read_file_with_line_numbers(tmp_path: Path):
    """Test that output includes line numbers."""
    path = tmp_path / "test.py"
    path.write_text('print("hello")\nprint("world")\n')

    messages = list(execute_read(None, [str(path)], None))
    assert len(messages) == 1
    content = messages[0].content
    assert "1\t" in content
    assert "2\t" in content


def test_read_file_line_range_kwargs(tmp_path: Path):
    """Test reading a line range via kwargs."""
    path = tmp_path / "test.txt"
    path.write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")

    messages = list(
        execute_read(
            None,
            None,
            {"path": str(path), "start_line": "2", "end_line": "4"},
        )
    )
    assert len(messages) == 1
    content = messages[0].content
    assert "line 2" in content
    assert "line 3" in content
    assert "line 4" in content
    assert "line 1" not in content
    assert "line 5" not in content
    assert "lines 2-4 of 5" in content


def test_read_file_start_line_only(tmp_path: Path):
    """Test reading from a specific start line to end via kwargs."""
    path = tmp_path / "test.txt"
    path.write_text("line 1\nline 2\nline 3\n")

    messages = list(
        execute_read(
            None,
            None,
            {"path": str(path), "start_line": "2"},
        )
    )
    assert len(messages) == 1
    content = messages[0].content
    assert "line 2" in content
    assert "line 3" in content
    assert "line 1" not in content


def test_read_nonexistent_file(tmp_path: Path):
    """Test reading a file that doesn't exist."""
    path = tmp_path / "nonexistent.txt"
    messages = list(execute_read(None, [str(path)], None))
    assert len(messages) == 1
    assert "File not found" in messages[0].content


def test_read_directory(tmp_path: Path):
    """Test reading a directory (should fail gracefully)."""
    messages = list(execute_read(None, [str(tmp_path)], None))
    assert len(messages) == 1
    assert "Not a file" in messages[0].content


def test_read_binary_file(tmp_path: Path):
    """Test reading a binary file."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x00\x01\x02\xff\xfe")
    messages = list(execute_read(None, [str(path)], None))
    assert len(messages) == 1
    assert "Cannot read binary file" in messages[0].content


def test_read_empty_file(tmp_path: Path):
    """Test reading an empty file."""
    path = tmp_path / "empty.txt"
    path.write_text("")
    messages = list(execute_read(None, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"


def test_read_via_kwargs(tmp_path: Path):
    """Test reading with only kwargs (tool format)."""
    path = tmp_path / "test.txt"
    path.write_text("hello\nworld\n")

    messages = list(execute_read(None, None, {"path": str(path)}))
    assert len(messages) == 1
    assert "hello" in messages[0].content
    assert "world" in messages[0].content


def test_read_no_path():
    """Test reading with no path provided."""
    messages = list(execute_read(None, None, None))
    assert len(messages) == 1
    assert "No path provided" in messages[0].content
