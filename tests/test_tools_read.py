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
    """Test reading a directory shows a listing."""
    # Create some files and a subdirectory
    (tmp_path / "hello.py").write_text("print('hello')")
    (tmp_path / "readme.md").write_text("# Hello")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested")

    messages = list(execute_read(None, [str(tmp_path)], None))
    assert len(messages) == 1
    content = messages[0].content
    # Directories listed with trailing slash, sorted first
    assert "subdir/" in content
    assert "hello.py" in content
    assert "readme.md" in content
    assert "3 entries" in content


def test_read_empty_directory(tmp_path: Path):
    """Test reading an empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    messages = list(execute_read(None, [str(empty_dir)], None))
    assert len(messages) == 1
    assert "empty directory" in messages[0].content


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


def test_read_multiple_files_via_code(tmp_path: Path):
    """Test batch reading: multiple paths in the code block, one per line."""
    p1 = tmp_path / "a.txt"
    p2 = tmp_path / "b.txt"
    p1.write_text("alpha 1\nalpha 2\n")
    p2.write_text("beta 1\nbeta 2\n")

    code = f"{p1}\n{p2}\n"
    messages = list(execute_read(code, None, None))

    # One message per file.
    assert len(messages) == 2
    assert "alpha 1" in messages[0].content
    assert "alpha 2" in messages[0].content
    assert "beta 1" in messages[1].content
    assert "beta 2" in messages[1].content


def test_read_multiple_files_skips_blanks_and_comments(tmp_path: Path):
    """Test batch reading ignores blank lines and # comments."""
    p1 = tmp_path / "a.txt"
    p2 = tmp_path / "b.txt"
    p1.write_text("alpha\n")
    p2.write_text("beta\n")

    code = f"\n# header comment\n{p1}\n\n# another comment\n{p2}\n"
    messages = list(execute_read(code, None, None))

    assert len(messages) == 2
    assert "alpha" in messages[0].content
    assert "beta" in messages[1].content


def test_read_multiple_files_partial_failure(tmp_path: Path):
    """Test batch reading: missing files don't abort the whole batch."""
    p1 = tmp_path / "exists.txt"
    p1.write_text("hello\n")
    missing = tmp_path / "missing.txt"
    p3 = tmp_path / "also-exists.txt"
    p3.write_text("world\n")

    code = f"{p1}\n{missing}\n{p3}\n"
    messages = list(execute_read(code, None, None))

    assert len(messages) == 3
    assert "hello" in messages[0].content
    assert "File not found" in messages[1].content
    assert "world" in messages[2].content


def test_read_multiple_files_ignores_line_range(tmp_path: Path):
    """Test that start_line/end_line is ignored when reading multiple files."""
    p1 = tmp_path / "a.txt"
    p2 = tmp_path / "b.txt"
    p1.write_text("a1\na2\na3\n")
    p2.write_text("b1\nb2\nb3\n")

    code = f"{p1}\n{p2}\n"
    messages = list(execute_read(code, None, {"start_line": "2"}))

    # First message is the notice, then one per file (full content).
    assert len(messages) == 3
    assert "ignored when reading multiple paths" in messages[0].content
    assert "a1" in messages[1].content and "a3" in messages[1].content
    assert "b1" in messages[2].content and "b3" in messages[2].content


def test_read_single_path_via_code_unchanged(tmp_path: Path):
    """Single-path code block keeps existing behavior."""
    p = tmp_path / "test.txt"
    p.write_text("only line\n")

    messages = list(execute_read(str(p), None, None))
    assert len(messages) == 1
    assert "only line" in messages[0].content
