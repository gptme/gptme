from gptme.util.content import is_message_command


def test_is_message_command():
    """Test the is_message_command helper function."""

    # Commands (single slash in first word)
    assert is_message_command("/shell echo hello")
    assert is_message_command("/log")
    assert is_message_command("/python print('hello')")
    assert is_message_command("/help")
    assert is_message_command("/exit")
    assert is_message_command("/shell main.py")  # Original issue case

    # File paths (multiple slashes)
    assert not is_message_command("/path/to/file.md")
    assert not is_message_command("/home/user/documents/file.txt")
    assert not is_message_command("/usr/bin/python")

    # Not commands
    assert not is_message_command("hello world")
    assert not is_message_command("regular message")
    assert not is_message_command("")

    # Edge cases
    assert not is_message_command("shell echo hello")  # No leading slash
    assert is_message_command("/")  # Just a slash
    assert is_message_command("/a")  # Single character command


def test_is_message_command_integration():
    """Test that the command detection is properly integrated."""
    import tempfile
    from pathlib import Path

    from gptme.message import Message
    from gptme.util.context import include_paths

    # Create a temp file to test with
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("print('Hello, World!')\n")
        temp_file = Path(f.name)

    try:
        # Test that /shell command doesn't trigger file reading
        msg = Message("user", f"/shell {temp_file.name}")
        result = include_paths(msg)
        # The message should be unchanged (not expanded with file contents)
        assert result.content == msg.content
        assert "Hello, World!" not in result.content

        # Test that a file path without /command does trigger file reading
        msg = Message("user", str(temp_file))
        result = include_paths(msg)
        # The message should be expanded with file contents
        assert "Hello, World!" in result.content

    finally:
        # Clean up
        temp_file.unlink()
