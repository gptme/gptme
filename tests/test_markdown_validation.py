"""Tests for markdown validation hook."""

from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.markdown_validation import (
    check_markdown_ending,
    validate_markdown_on_message_complete,
)


def test_check_markdown_ending_header():
    """Test detection of incomplete headers."""
    content = "Some content\n# Incomplete Header"
    is_suspicious, pattern = check_markdown_ending(content)
    assert is_suspicious
    assert pattern is not None
    assert "header start" in pattern


def test_check_markdown_ending_colon():
    """Test detection of incomplete content with colons."""
    content = "Some content\nTitle:"
    is_suspicious, pattern = check_markdown_ending(content)
    assert is_suspicious
    assert pattern is not None
    assert "colon" in pattern


def test_check_markdown_ending_valid():
    """Test that valid endings are not flagged."""
    valid_contents = [
        "Normal paragraph ending.",
        "Code block:\n```python\nprint('hello')\n```",
        "List:\n- Item 1\n- Item 2",
        "",  # Empty content
    ]

    for content in valid_contents:
        is_suspicious, pattern = check_markdown_ending(content)
        assert not is_suspicious, f"Should not flag: {content!r}"


def test_check_markdown_ending_empty():
    """Test handling of empty content."""
    is_suspicious, pattern = check_markdown_ending("")
    assert not is_suspicious

    is_suspicious, pattern = check_markdown_ending("   \n  \n  ")
    assert not is_suspicious


def test_validate_markdown_hook_detects_issue(tmp_path):
    """Test that hook detects suspicious endings and yields warning."""
    manager = LogManager(logdir=tmp_path, lock=False)

    # Add assistant message with suspicious ending
    manager.append(Message("assistant", "Some content\nTitle:"))

    # Run the hook
    results = list(validate_markdown_on_message_complete(manager))
    messages = [msg for msg in results if isinstance(msg, Message)]

    # Should yield a warning message
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Potential markdown codeblock cut-off" in messages[0].content
    assert "colon" in messages[0].content


def test_validate_markdown_hook_ignores_valid(tmp_path):
    """Test that hook doesn't warn on valid content."""
    manager = LogManager(logdir=tmp_path, lock=False)

    # Add assistant message with valid ending
    manager.append(Message("assistant", "This is valid content."))

    # Run the hook
    messages = list(validate_markdown_on_message_complete(manager))

    # Should not yield any warnings
    assert len(messages) == 0


def test_validate_markdown_hook_ignores_user_messages(tmp_path):
    """Test that hook only checks assistant messages."""
    manager = LogManager(logdir=tmp_path, lock=False)

    # Add user message with suspicious ending (should be ignored)
    manager.append(Message("user", "What about\nTitle:"))

    # Run the hook
    messages = list(validate_markdown_on_message_complete(manager))

    # Should not yield warnings for user messages
    assert len(messages) == 0


def test_validate_markdown_hook_empty_log(tmp_path):
    """Test that hook handles empty logs gracefully."""
    manager = LogManager(logdir=tmp_path, lock=False)

    # Don't add any messages

    # Run the hook - should not raise
    messages = list(validate_markdown_on_message_complete(manager))

    assert len(messages) == 0
