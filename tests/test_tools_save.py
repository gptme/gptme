"""Tests for the save and append tools."""

from pathlib import Path


from gptme.tools.save import execute_append, execute_save


def test_save_tool(tmp_path: Path):
    """Test the save tool."""
    # Test saving a new file
    path = tmp_path / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving an existing file
    content = "Hello again!\n"
    messages = list(execute_save(content, [str(path)], None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test saving to a non-existent directory
    path = tmp_path / "subdir" / "test.txt"
    content = "Hello, world!\n"
    messages = list(execute_save(content, [str(path)], None, lambda _: True))
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
        messages = list(execute_save(content, [str(path)], None, lambda _: True))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content


def test_append_tool(tmp_path: Path):
    """Test the append tool."""
    path = tmp_path / "test.txt"

    # Test appending to a new file
    content = "First line\n"
    messages = list(execute_append(content, [str(path)], None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert path.read_text() == content

    # Test appending to existing file
    content2 = "Second line\n"
    messages = list(execute_append(content2, [str(path)], None, lambda _: True))
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
        messages = list(execute_append(content, [str(path)], None, lambda _: True))
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "placeholder lines" in messages[0].content
