from gptme.message import Message, msgs_to_toml, toml_to_msgs


def test_toml():
    # single message, check escaping
    msg = Message(
        "system",
        '''Hello world!
"""Difficult to handle string"""''',
    )
    t = msg.to_toml()
    print(t)
    m = Message.from_toml(t)
    print(m)
    assert msg.content == m.content
    assert msg.role == m.role
    assert msg.timestamp.date() == m.timestamp.date()
    assert msg.timestamp.timetuple() == m.timestamp.timetuple()

    # multiple messages
    msg2 = Message("user", "Hello computer!", pinned=True, hide=True)
    ts = msgs_to_toml([msg, msg2])
    print(ts)
    ms = toml_to_msgs(ts)
    print(ms)
    assert len(ms) == 2
    assert ms[0].role == msg.role
    assert ms[0].timestamp.timetuple() == msg.timestamp.timetuple()
    assert ms[0].content == msg.content
    assert ms[1].content == msg2.content

    # check flags
    assert ms[1].pinned == msg2.pinned
    assert ms[1].hide == msg2.hide


def test_get_codeblocks():
    # single codeblock only
    msg = Message(
        "system",
        """```ipython
def test():
    print("Hello world!")
```""",
    )
    codeblocks = msg.get_codeblocks()
    assert len(codeblocks) == 1

    # multiple codeblocks and leading/trailing text
    msg = Message(
        "system",
        """Hello world!

```bash
echo "Hello world!"
```

```ipython
print("Hello world!")
```

That's all folks!
""",
    )
    codeblocks = msg.get_codeblocks()
    assert len(codeblocks) == 2


def test_format_msgs_escapes_rich_markup():
    """Test that Rich markup is properly escaped in format_msgs."""
    from gptme.message import Message, format_msgs

    # Test with content containing Rich-like markup that should be escaped
    msg = Message("user", "Testing [project] with [bold]content[/bold]")

    # Without highlight - should not escape
    outputs_no_highlight = format_msgs([msg], highlight=False)
    assert len(outputs_no_highlight) == 1

    # With highlight - should escape markup
    outputs_highlight = format_msgs([msg], highlight=True)
    assert len(outputs_highlight) == 1
    # The escaped version should be different (escaped brackets)
    # Note: We can't directly check the escape as it's in the Rich formatted string
    # but we verify no exception is raised from Rich interpreting brackets as tags


def test_format_msgs_oneline_escapes_rich_markup():
    """Test that Rich markup is escaped in oneline mode."""
    from gptme.message import Message, format_msgs

    msg = Message("user", "Testing [project]\nwith newlines")

    # With highlight and oneline
    outputs = format_msgs([msg], oneline=True, highlight=True)
    assert len(outputs) == 1
    # Verify no Rich markup interpretation error


def test_format_msgs_preserves_codeblocks():
    """Test that code blocks are not escaped (for syntax highlighting)."""
    from gptme.message import Message, format_msgs

    msg = Message("user", "```python\n[1, 2, 3]\n```")

    outputs = format_msgs([msg], highlight=True)
    assert len(outputs) == 1
    # Code blocks should still work with syntax highlighting


def test_message_files_resolve_to_absolute(tmp_path, monkeypatch):
    """Test that file paths are resolved to absolute paths when serializing.

    This prevents issues when the working directory changes after attaching
    files to a message. See issue #262.
    """
    import os
    from pathlib import Path

    from gptme.message import Message

    # Create a test file in tmp_path
    test_file = tmp_path / "test_image.png"
    test_file.write_bytes(b"fake image data")

    # Change to tmp_path and create a message with a relative path
    original_cwd = os.getcwd()
    try:
        monkeypatch.chdir(tmp_path)
        msg = Message("user", "Check this image", files=[Path("test_image.png")])

        # Serialize the message
        d = msg.to_dict()

        # The file path should be absolute in the serialized dict
        assert d["files"][0] == str(test_file.resolve())
        assert Path(d["files"][0]).is_absolute()

        # Change to a different directory
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)

        # Deserialize and verify the path still works
        # (simulating what logmanager._gen_read_jsonl does)
        loaded_files = [Path(f) for f in d.get("files", [])]
        loaded_msg = Message(
            d["role"],
            d["content"],
            files=loaded_files,
        )
        assert len(loaded_msg.files) == 1
        assert loaded_msg.files[0].exists()
    finally:
        os.chdir(original_cwd)


def test_toml_file_hashes():
    """Test that file_hashes survive TOML round-trip."""
    from pathlib import Path

    # Use full path as key (not just basename) to avoid collisions
    msg = Message(
        "user",
        "Check this file",
        files=[Path("/tmp/test.py")],
        file_hashes={"/tmp/test.py": "abc123def456"},
    )

    # Round-trip through TOML
    toml_str = msg.to_toml()
    loaded = Message.from_toml(toml_str)

    # Verify file_hashes survived
    assert loaded.file_hashes == msg.file_hashes
    assert loaded.file_hashes.get("/tmp/test.py") == "abc123def456"


def test_file_hashes_no_collision_same_basename():
    """Test that files with same basename but different paths don't collide."""
    from pathlib import Path

    # Two files with the same basename but different paths
    msg = Message(
        "user",
        "Check these files",
        files=[Path("/src/utils/test.py"), Path("/tests/test.py")],
        file_hashes={
            "/src/utils/test.py": "hash_for_src_utils",
            "/tests/test.py": "hash_for_tests",
        },
    )

    # Both files should have distinct hashes
    assert len(msg.file_hashes) == 2
    assert msg.file_hashes.get("/src/utils/test.py") == "hash_for_src_utils"
    assert msg.file_hashes.get("/tests/test.py") == "hash_for_tests"

    # Round-trip through TOML should preserve both
    toml_str = msg.to_toml()
    loaded = Message.from_toml(toml_str)
    assert loaded.file_hashes == msg.file_hashes
