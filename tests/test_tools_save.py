"""Tests for the save and append tools."""

from pathlib import Path

from gptme.message import Message
from gptme.tools import save as save_tool
from gptme.tools.save import (
    _get_preview_lang,
    _read_text_safe,
    execute_append,
    execute_save,
    preview_append,
    preview_save,
)


def test_save_tool(tmp_path: Path):
    """Test the save tool."""
    # Test saving a new file
    path = tmp_path / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving an existing file
    content = "Hello again!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving to a non-existent directory
    path = tmp_path / "subdir" / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content


def test_save_tool_placeholders(tmp_path: Path):
    """Test that the save tool detects placeholder lines."""
    path = tmp_path / "test.txt"
    placeholders = [
        "# ... rest of content goes here",
        "// ... rest of content goes here",
        "# ...",
        "// ...",
        '""" ... """',
        "# ... rest of the content is the same ...",
    ]
    for placeholder in placeholders:
        content = f"First line\n{placeholder}\nLast line\n"
        messages = list(execute_save(content, [str(path)], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content


def test_append_tool(tmp_path: Path):
    """Test the append tool."""
    path = tmp_path / "test.txt"

    # Test appending to a new file
    content = "First line\n"
    messages = list(execute_append(content, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test appending to existing file
    content2 = "Second line\n"
    messages = list(execute_append(content2, [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content + content2


def test_append_tool_placeholders(tmp_path: Path):
    """Test that the append tool detects placeholder lines."""
    path = tmp_path / "test.txt"
    path.write_text("Existing content\n")

    placeholders = [
        "# ... rest of content goes here",
        "// ... rest of content goes here",
        "# ...",
        "// ...",
        '""" ... """',
        "# ... rest of the content is the same ...",
    ]
    for placeholder in placeholders:
        content = f"First line\n{placeholder}\nLast line\n"
        messages = list(execute_append(content, [str(path)], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content


def test_save_tool_path_traversal_relative(tmp_path: Path):
    """Test that path traversal via relative paths is blocked."""
    import os

    # Change to tmp_path so we can test relative paths
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Create a subdirectory to work from
        subdir = tmp_path / "work"
        subdir.mkdir()
        os.chdir(subdir)

        # Try to escape with ../ - should return error message
        messages = list(execute_save("test", ["../../escape.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal detected" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_save_tool_path_traversal_symlink(tmp_path: Path):
    """Test that symlink-based path traversal is blocked."""
    import os

    # Change to tmp_path so cwd is known
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        # Create a directory outside the cwd
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # Create work directory
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Create a symlink in work_dir pointing to outside_dir
        symlink = work_dir / "escape_link"
        symlink.symlink_to(outside_dir)

        # Try to save through symlink - should return error message
        messages = list(execute_save("test", ["escape_link/secret.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal detected" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_append_tool_path_traversal_relative(tmp_path: Path):
    """Test that path traversal via relative paths is blocked for append."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        subdir = tmp_path / "work"
        subdir.mkdir()
        os.chdir(subdir)

        # Should return error message (matches save tool behavior)
        messages = list(execute_append("test", ["../../escape.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_append_tool_path_traversal_symlink(tmp_path: Path):
    """Test that symlink-based path traversal is blocked for append."""
    import os

    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        os.chdir(work_dir)

        # Create a symlink in work_dir pointing to outside_dir
        symlink = work_dir / "escape_link"
        symlink.symlink_to(outside_dir)

        # Try to append through symlink - should return error message
        messages = list(execute_append("test", ["escape_link/secret.txt"], None))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Path traversal" in messages[0].content
    finally:
        os.chdir(original_cwd)


def test_read_text_safe_utf8(tmp_path: Path):
    """Test _read_text_safe with normal UTF-8 file."""
    path = tmp_path / "normal.txt"
    path.write_text("hello world\n")
    assert _read_text_safe(path) == "hello world\n"


def test_read_text_safe_binary(tmp_path: Path):
    """Test _read_text_safe returns None for binary files."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    assert _read_text_safe(path) is None


def test_read_text_safe_nonexistent(tmp_path: Path):
    """Test _read_text_safe returns None for missing files."""
    path = tmp_path / "missing.txt"
    assert _read_text_safe(path) is None


def test_preview_save_binary_file(tmp_path: Path):
    """Test that preview_save handles binary files gracefully."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    # When overwriting a binary file with text, show the full new content
    result = preview_save("new text content", path)
    assert result == "new text content"


def test_preview_append_binary_file(tmp_path: Path):
    """Test that preview_append handles binary files gracefully."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    result = preview_append("appended text", path)
    assert result == "appended text"


def test_save_overwrites_binary_file(tmp_path: Path):
    """Test that save can overwrite a binary file without crashing."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    messages = list(execute_save("new content", [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Saved to" in messages[0].content
    assert path.read_text() == "new content\n"


def test_append_to_binary_file(tmp_path: Path):
    """Test that append handles binary files without crashing."""
    path = tmp_path / "test.txt"
    # Write valid text first, then corrupt it with binary
    path.write_bytes(b"existing\xff\xfe")
    messages = list(execute_append("new line", [str(path)], None))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Appended to" in messages[0].content
    assert path.read_bytes() == b"existing\xff\xfenew line\n"


def test_get_preview_lang_binary_file(tmp_path: Path):
    """Test that binary files don't get diff preview highlighting."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    assert _get_preview_lang(path) is None


def test_execute_save_skips_diff_preview_for_binary_file(tmp_path: Path, monkeypatch):
    """Test that save passes no preview language for binary files."""
    path = tmp_path / "binary.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe")
    captured: dict[str, str | None] = {}

    def fake_execute_with_confirmation(*args, **kwargs):
        captured["preview_lang"] = kwargs["preview_lang"]
        yield Message("system", "stub")

    monkeypatch.setattr(
        save_tool, "execute_with_confirmation", fake_execute_with_confirmation
    )

    messages = list(save_tool.execute_save("new content", [str(path)], None))

    assert len(messages) == 1
    assert messages[0].content == "stub"
    assert captured["preview_lang"] is None
