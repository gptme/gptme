"""Tests for markdown validation hook."""

import pytest

from gptme.hooks import clear_hooks
from gptme.hooks.markdown_validation import (
    check_last_line_suspicious,
    validate_markdown_on_message_complete,
)
from gptme.logmanager import LogManager
from gptme.message import Message


@pytest.fixture(autouse=True)
def setup_hooks(init_):
    """Clear all hooks and re-register markdown validation hook for each test.

    Depends on init_ fixture to ensure tools are loaded first.
    """
    from gptme.hooks import HookType, register_hook

    # Clear all hooks
    clear_hooks()

    # Re-register the markdown validation hook that was cleared
    register_hook(
        "markdown_validation",
        HookType.MESSAGE_POST_PROCESS,
        validate_markdown_on_message_complete,
        priority=1,
    )

    yield

    # Clean up after test
    clear_hooks()


# Triple backticks for markdown codeblocks in test data
ticks = "```"


def test_check_last_line_suspicious_header():
    """Test detection of incomplete headers."""
    content = "Some content\n# Incomplete Header"
    is_suspicious, pattern = check_last_line_suspicious(content)
    assert is_suspicious
    assert pattern is not None
    assert "header start" in pattern


def test_check_last_line_suspicious_colon():
    """Test that content ending with colon is NOT flagged (removed due to false positives)."""
    content = "Some content\nTitle:"
    is_suspicious, pattern = check_last_line_suspicious(content)
    assert not is_suspicious
    assert pattern is None


def test_check_last_line_suspicious_valid():
    """Test that valid endings are not flagged."""
    valid_contents = [
        "Normal paragraph ending.",
        "Code block:\n```python\nprint('hello')\n```",
        "List:\n- Item 1\n- Item 2",
        "",  # Empty content
    ]

    for content in valid_contents:
        is_suspicious, pattern = check_last_line_suspicious(content)
        assert not is_suspicious, f"Should not flag: {content!r}"


def test_check_last_line_suspicious_empty():
    """Test handling of empty content."""
    is_suspicious, pattern = check_last_line_suspicious("")
    assert not is_suspicious

    is_suspicious, pattern = check_last_line_suspicious("   \n  \n  ")
    assert not is_suspicious


@pytest.mark.flaky(retries=3, delay=1)
def test_validate_markdown_hook_detects_issue():
    """Test that hook detects suspicious endings in markdown tooluse."""
    manager = LogManager(lock=False)

    # Add assistant message with markdown tooluse that has suspicious ending (incomplete header)
    message_content = f"""Let me create a file.

{ticks}save test.txt
Some content
#
{ticks}"""
    manager.append(Message("assistant", message_content))

    # Run the hook
    results = list(validate_markdown_on_message_complete(manager))
    messages = [msg for msg in results if isinstance(msg, Message)]

    # Should yield a warning message
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Potential markdown codeblock cut-off" in messages[0].content
    assert "header" in messages[0].content


def test_validate_markdown_hook_ignores_valid():
    """Test that hook doesn't warn on valid content."""
    manager = LogManager(lock=False)

    # Add assistant message with valid ending
    manager.append(Message("assistant", "This is valid content."))

    # Run the hook
    messages = list(validate_markdown_on_message_complete(manager))

    # Should not yield any warnings
    assert len(messages) == 0


def test_validate_markdown_hook_ignores_user_messages():
    """Test that hook only checks assistant messages."""
    manager = LogManager(lock=False)

    # Add user message with suspicious ending (should be ignored)
    manager.append(Message("user", "What about\nTitle:"))

    # Run the hook
    messages = list(validate_markdown_on_message_complete(manager))

    # Should not yield warnings for user messages
    assert len(messages) == 0


def test_validate_markdown_hook_empty_log():
    """Test that hook handles empty logs gracefully."""
    manager = LogManager(lock=False)

    # Don't add any messages

    # Run the hook - should not raise
    messages = list(validate_markdown_on_message_complete(manager))

    assert len(messages) == 0
