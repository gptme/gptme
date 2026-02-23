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
