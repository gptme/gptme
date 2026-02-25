from pathlib import Path

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


def test_include_paths_image_auto_attach(tmp_path):
    """Image files in user messages should be auto-attached to msg.files.

    This verifies the full pipeline: _find_potential_paths detects the path,
    _parse_prompt_files validates it as a supported binary format, and
    include_paths adds it to msg.files (not embedded as text content).
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

    # Create a minimal PNG file (valid header)
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # User message with a bare image path
    msg = Message("user", str(img_file))
    result = include_paths(msg, workspace=None)

    # Image should be in msg.files (not embedded as text)
    assert len(result.files) == 1
    assert Path(str(result.files[0])).name == "test.png"
    # Original content should be preserved (not modified)
    assert str(img_file) in result.content


def test_include_paths_image_in_text(tmp_path):
    """Image paths embedded in natural language text should be auto-attached.

    Simulates the scenario where a user types 'View this image ~/test.png'
    or a paste handler inserts 'View this image: /path/to/image.png'.
    """
    from gptme.message import Message
    from gptme.util.context import include_paths

    # Create a minimal PNG file
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # User message with image path embedded in text (like paste handler output)
    msg = Message("user", f"View this image: {img_file}")
    result = include_paths(msg, workspace=None)

    # Image should be auto-attached to msg.files
    assert len(result.files) == 1
    assert Path(str(result.files[0])).name == "screenshot.png"


def test_embed_attached_preserves_image_files(tmp_path):
    """Images in msg.files should survive embed_attached_file_content.

    Text files get embedded as codeblocks and removed from msg.files.
    Image files (binary) should remain in msg.files for provider-specific
    handling (base64 encoding in _process_file).
    """
    from gptme.message import Message
    from gptme.util.context import embed_attached_file_content

    # Create both a text file and an image file
    text_file = tmp_path / "readme.txt"
    text_file.write_text("hello world")

    img_file = tmp_path / "photo.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    msg = Message("user", "Check these files", files=[text_file, img_file])
    result = embed_attached_file_content(msg, workspace=tmp_path)

    # Text file should be embedded in content and removed from files
    assert "hello world" in result.content
    assert not any(Path(str(f)).name == "readme.txt" for f in result.files)

    # Image file should remain in files (not embedded)
    assert any(Path(str(f)).name == "photo.png" for f in result.files)


def test_image_auto_attach_end_to_end(tmp_path):
    """End-to-end test: image path in user text → include_paths → embed → msgs2dicts.

    Verifies that an image mentioned by path in a user message survives the
    full message processing pipeline and appears in the final dict's files list.
    """
    from gptme.message import Message, msgs2dicts
    from gptme.util.context import embed_attached_file_content, include_paths

    # Create a minimal PNG
    img_file = tmp_path / "paste_20260225.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Step 1: include_paths extracts the image path
    msg = Message("user", str(img_file))
    msg = include_paths(msg, workspace=None)
    assert len(msg.files) == 1, "include_paths should detect and attach image"

    # Step 2: embed_attached_file_content preserves images
    msg = embed_attached_file_content(msg, workspace=None)
    assert len(msg.files) == 1, (
        "embed should preserve image in files (not embed as text)"
    )

    # Step 3: msgs2dicts preserves files for provider processing
    dicts = msgs2dicts([msg])
    assert "files" in dicts[0], "files should be present in message dict"
    assert len(dicts[0]["files"]) == 1, "image file should survive serialization"
