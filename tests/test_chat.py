from gptme.util.context import _find_potential_paths


def test_find_potential_paths(tmp_path, monkeypatch):
    # Create some test files
    (tmp_path / "test.txt").touch()
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir/file.py").touch()

    # Change to temp directory for testing
    monkeypatch.chdir(tmp_path)

    # Test various path formats
    content = """
Here are some paths:
/absolute/path
~/home/path
./relative/path
test.txt
subdir/file.py
http://example.com
https://example.com/path

```python
# This path should be ignored
ignored_path = "/path/in/codeblock"
```

More text with `wrapped/path` and path.with.dots
        """

    paths = _find_potential_paths(content)

    # Check expected paths are found
    assert "/absolute/path" in paths
    assert "~/home/path" in paths
    assert "./relative/path" in paths
    assert "test.txt" in paths  # exists in tmp_path
    assert "subdir/file.py" in paths  # exists in tmp_path
    assert "http://example.com" in paths
    assert "https://example.com/path" in paths
    assert "wrapped/path" in paths

    # Check paths in codeblocks are ignored
    assert "/path/in/codeblock" not in paths

    # Check non-paths are ignored
    assert "path.with.dots" not in paths


def test_find_potential_paths_empty():
    # Test with empty content
    assert _find_potential_paths("") == []

    # Test with no paths
    assert _find_potential_paths("just some text") == []


def test_include_paths_skips_system_messages():
    """Test that include_paths skips role=system messages (tool output) entirely."""
    from gptme.message import Message
    from gptme.util.context import include_paths

    # A system message with path-like content (e.g. tool output)
    content = """
<tool_use>
<cmd>cat /path/inside/tool/output.txt</cmd>
</tool_use>

<result>
Content from /path/in/result/data.csv
</result>

Also /some/path/in/system/message.txt
    """

    msg = Message("system", content)
    result = include_paths(msg)

    # system messages should be returned unchanged (no paths extracted)
    assert result == msg
    assert result.files == []


def test_find_potential_paths_punctuation():
    # Test paths with trailing punctuation
    content = """
    Look at ~/file.txt!
    Check /path/to/file?
    See ./local/path.
    Visit https://example.com,
    """

    paths = _find_potential_paths(content)
    assert "~/file.txt" in paths
    assert "/path/to/file" in paths
    assert "./local/path" in paths
    assert "https://example.com" in paths


def test_embed_attached_file_content_separator(tmp_path):
    """File contents should be separated from message content by double newlines."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create a test file
    test_file = tmp_path / "test.py"
    test_file.write_text("print('hello')")

    # Message with content and an attached file
    msg = Message("user", "Check this file", files=[test_file])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # The file content should be separated from the message content
    assert result.content.startswith("Check this file\n\n")
    assert "print('hello')" in result.content
    # File should be removed from files list (embedded as text)
    assert test_file not in result.files


def test_embed_attached_file_content_multiple_files(tmp_path):
    """Multiple embedded files should each be separated by double newlines."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create test files
    file_a = tmp_path / "a.py"
    file_a.write_text("code_a")
    file_b = tmp_path / "b.py"
    file_b.write_text("code_b")

    msg = Message("user", "Review these", files=[file_a, file_b])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # Both files should be embedded with proper separation
    assert result.content.startswith("Review these\n\n")
    assert "code_a" in result.content
    assert "code_b" in result.content
    # The two codeblocks should be separated by double newlines
    assert "\n\n````" in result.content


def test_embed_attached_file_content_no_files():
    """Message without files should be returned unchanged."""
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    msg = Message("user", "No files here")
    result = embed_attached_file_content(msg)

    assert result.content == "No files here"


def test_parse_prompt_files_long_string():
    """Long strings that exceed filesystem limits should return None, not raise."""
    from gptme.util.context import _parse_prompt_files

    # A string that's too long to be a valid path (most systems limit to ~4096 chars)
    long_string = "/" + "a" * 5000

    # Should return None (not a path), not raise OSError
    result = _parse_prompt_files(long_string)
    assert result is None


def test_find_potential_paths_ignores_xml_tags():
    """Paths inside XML tags should not be extracted (e.g. user pastes tool output)."""
    content = """
Here is some user text mentioning /real/path/to/file.txt.

<tool_use>
<cmd>cat /path/inside/xml/tag.txt</cmd>
</tool_use>

<result>
Contents from /another/xml/path.csv
</result>

Also check `./outside/xml.py` which should be found.
"""
    paths = _find_potential_paths(content)

    # Paths outside XML tags should be found
    assert "/real/path/to/file.txt" in paths
    assert "./outside/xml.py" in paths

    # Paths inside XML tags should be ignored
    assert "/path/inside/xml/tag.txt" not in paths
    assert "/another/xml/path.csv" not in paths
