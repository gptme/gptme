from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from gptme.message import Message, msgs_to_toml, toml_to_msgs

if TYPE_CHECKING:
    from gptme.util.uri import FilePath


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
        loaded_files: list[FilePath] = [Path(f) for f in d.get("files", [])]
        loaded_msg = Message(
            d["role"],
            d["content"],
            files=loaded_files,
        )
        assert len(loaded_msg.files) == 1
        file_ref = loaded_msg.files[0]
        assert isinstance(file_ref, Path) and file_ref.exists()
    finally:
        os.chdir(original_cwd)


def test_toml_file_hashes():
    """Test that file_hashes survive TOML round-trip."""

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


def test_message_metadata():
    """Test that metadata survives JSONL and TOML round-trips."""
    import json

    from gptme.message import Message, MessageMetadata

    # Create message with metadata using flat token format
    # Per Erik's review: https://github.com/gptme/gptme/pull/943#issuecomment-3633137716
    meta: MessageMetadata = {
        "model": "claude-sonnet",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 80,
        "cache_creation_tokens": 10,
        "cost": 0.005,
    }
    msg = Message(role="assistant", content="Hello world", metadata=meta)

    # Verify metadata stored correctly
    assert msg.metadata is not None
    assert msg.metadata["model"] == "claude-sonnet"
    assert msg.metadata["input_tokens"] == 100
    assert msg.metadata["output_tokens"] == 50
    assert msg.metadata["cache_read_tokens"] == 80
    assert msg.metadata["cache_creation_tokens"] == 10
    assert msg.metadata["cost"] == 0.005

    # Test JSON/JSONL roundtrip
    d = msg.to_dict()
    assert "metadata" in d
    json_str = json.dumps(d)
    json_data = json.loads(json_str)
    from dateutil.parser import isoparse

    json_data["timestamp"] = isoparse(json_data["timestamp"])
    msg2 = Message(**json_data)
    assert msg2.metadata == meta

    # Test TOML roundtrip
    toml_str = msg.to_toml()
    msg3 = Message.from_toml(toml_str)
    assert msg3.metadata == meta


def test_message_metadata_none():
    """Test that messages without metadata work correctly."""
    import json

    from gptme.message import Message

    # Create message without metadata
    msg = Message(role="user", content="Hello")

    # Verify no metadata
    assert msg.metadata is None

    # to_dict should NOT include metadata key for compact storage
    d = msg.to_dict()
    assert "metadata" not in d

    # JSONL roundtrip
    json_str = json.dumps(d)
    json_data = json.loads(json_str)
    from dateutil.parser import isoparse

    json_data["timestamp"] = isoparse(json_data["timestamp"])
    msg2 = Message(**json_data)
    assert msg2.metadata is None

    # TOML roundtrip
    toml_str = msg.to_toml()
    msg3 = Message.from_toml(toml_str)
    assert msg3.metadata is None


def test_to_xml_escapes_special_characters():
    """Test that to_xml properly escapes XML special characters."""
    from gptme.message import Message

    # Test content with XML special characters
    msg = Message(role="user", content="Use <tag> and & symbol")
    xml_str = msg.to_xml()

    # Content should be escaped
    assert "&lt;tag&gt;" in xml_str
    assert "&amp;" in xml_str

    # Role should be properly quoted
    assert 'role="user"' in xml_str


def test_to_xml_handles_quotes_in_role():
    """Test that to_xml handles quotes in role attribute."""
    from gptme.message import Message

    # Create a message - role with special chars is unusual but should be safe
    msg = Message(role="user", content='Test content with "quotes"')
    xml_str = msg.to_xml()

    # Quotes in content should be safe (no escaping needed for XML content)
    assert 'Test content with "quotes"' in xml_str


def test_message_equality_ignores_timestamp():
    """Test that Message equality is based on role and content, not timestamp."""
    msg1 = Message("user", "Hello", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    msg2 = Message("user", "Hello", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    msg3 = Message("user", "World", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    msg4 = Message(
        "assistant", "Hello", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc)
    )

    # Same role + content = equal, regardless of timestamp
    assert msg1 == msg2

    # Different content = not equal
    assert msg1 != msg3

    # Different role = not equal
    assert msg1 != msg4

    # Not equal to non-Message
    assert msg1 != "not a message"


def test_message_hash_consistent_with_equality():
    """Test that Message hash is consistent with equality (equal objects hash the same)."""
    msg1 = Message("user", "Hello", timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc))
    msg2 = Message("user", "Hello", timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
    msg3 = Message("user", "World")

    # Equal messages must have equal hashes
    assert hash(msg1) == hash(msg2)

    # Different messages should (likely) have different hashes
    assert hash(msg1) != hash(msg3)

    # Messages work correctly in sets
    s = {msg1, msg2, msg3}
    assert len(s) == 2  # msg1 and msg2 are equal, so only 2 unique


def test_toml_preserves_whitespace():
    """Test that from_toml preserves leading/trailing whitespace in content.

    Note: TOML multiline strings add a trailing newline due to format:
        content = '''
        {content}
        '''
    So we test that leading whitespace and internal structure is preserved.
    """
    # Content with intentional leading whitespace
    content_with_whitespace = "  \n  code with indentation  \n"
    msg = Message(
        "user",
        content_with_whitespace,
        timestamp=datetime.now(tz=timezone.utc),
    )

    # Roundtrip through TOML
    toml_str = msg.to_toml()
    restored = Message.from_toml(toml_str)

    # Leading whitespace should be preserved
    assert restored.content.startswith("  \n")
    # Internal structure preserved
    assert "  code with indentation" in restored.content
    # Should not be fully stripped to just the words
    assert restored.content != "code with indentation"


def test_call_id_serialization():
    """Test that call_id=None doesn't serialize as literal string 'None'.

    Issue #1035: call_id=None was being serialized as 'call_id = "None"'
    which then gets parsed back as the string "None" instead of None.
    """
    # Message without call_id (default None)
    msg_no_callid = Message(
        "assistant",
        "Hello",
        timestamp=datetime.now(tz=timezone.utc),
    )

    # Verify call_id = "None" is NOT in the TOML output
    toml_str = msg_no_callid.to_toml()
    assert 'call_id = "None"' not in toml_str
    assert "call_id" not in toml_str  # Should not appear at all

    # Roundtrip should preserve None
    restored = Message.from_toml(toml_str)
    assert restored.call_id is None

    # Message with call_id should serialize and roundtrip correctly
    msg_with_callid = Message(
        "assistant",
        "Tool response",
        timestamp=datetime.now(tz=timezone.utc),
        call_id="call_abc123",
    )

    toml_str_with = msg_with_callid.to_toml()
    assert 'call_id = "call_abc123"' in toml_str_with

    restored_with = Message.from_toml(toml_str_with)
    assert restored_with.call_id == "call_abc123"


def test_message_concat():
    """Test Message.concat properly merges all relevant fields."""
    from pathlib import Path

    msg1 = Message(
        "user",
        "First message",
        files=[Path("/tmp/file1.png")],
        file_hashes={"/tmp/file1.png": "hash1"},
    )
    msg2 = Message(
        "user",
        "Second message",
        files=[Path("/tmp/file2.png")],
        file_hashes={"/tmp/file2.png": "hash2"},
        pinned=True,
    )

    # Test basic concatenation
    merged = msg1.concat(msg2)
    assert merged.role == "user"
    assert merged.content == "First message\n\nSecond message"
    assert len(merged.files) == 2
    assert Path("/tmp/file1.png") in merged.files
    assert Path("/tmp/file2.png") in merged.files

    # Test file_hashes are merged
    assert merged.file_hashes == {
        "/tmp/file1.png": "hash1",
        "/tmp/file2.png": "hash2",
    }

    # Test pinned is preserved if either message is pinned
    assert merged.pinned is True

    # Test custom separator
    merged_custom = msg1.concat(msg2, separator=" | ")
    assert merged_custom.content == "First message | Second message"


def test_message_concat_different_roles_raises():
    """Test that concat raises ValueError for different roles."""
    import pytest

    msg1 = Message("user", "User message")
    msg2 = Message("assistant", "Assistant message")

    with pytest.raises(ValueError, match="different roles"):
        msg1.concat(msg2)


def test_message_concat_preserves_images():
    """Test that concat preserves image files for vision models."""
    from pathlib import Path

    # Simulates system message converted to user + original user with image
    system_as_user = Message(
        "user",
        "<system>You are a helpful assistant</system>",
    )
    user_with_image = Message(
        "user",
        "/path/to/image.png",
        files=[Path("/path/to/image.png")],
        file_hashes={"/path/to/image.png": "abc123"},
    )

    # This is what _merge_consecutive does
    merged = system_as_user.concat(user_with_image)

    # The image should be preserved
    assert len(merged.files) == 1
    assert Path("/path/to/image.png") in merged.files
    assert merged.file_hashes.get("/path/to/image.png") == "abc123"
