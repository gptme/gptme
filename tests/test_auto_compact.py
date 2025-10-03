"""
Tests for auto-compacting functionality that handles conversations with massive tool results.
"""

from datetime import datetime

import pytest
from gptme.llm.models import get_default_model, get_model
from gptme.message import Message, len_tokens
from gptme.tools.autocompact import (
    _create_tool_result_summary,
    auto_compact_log,
    should_auto_compact,
)


def create_test_conversation():
    """Create a test conversation with a massive tool result that works with any model."""
    # Create content that will definitely trigger auto-compacting
    model = get_default_model() or get_model("gpt-4")
    target_tokens = int(0.85 * model.context)  # 85% of context limit

    # Create a very large tool output with varied content that tokenizes to target_tokens
    # Use varied text so it doesn't compress well during tokenization
    words = [
        f"file_{i}.txt" for i in range(target_tokens // 2)
    ]  # ~2 tokens per filename
    repeated_content = "\n".join(words)
    tool_output = f"Ran command: `find /usr -type f`\n{repeated_content}"

    return [
        Message("user", "Please run a command to list files", datetime.now()),
        Message("assistant", "I'll run the ls command for you.", datetime.now()),
        Message("system", tool_output, datetime.now()),
    ]


def test_should_auto_compact_with_massive_tool_result():
    """Test that should_auto_compact correctly identifies conversations needing auto-compacting."""
    messages = create_test_conversation()

    # Should trigger auto-compacting due to massive tool result + being close to limit
    assert should_auto_compact(messages)


def test_should_auto_compact_with_small_messages():
    """Test that should_auto_compact doesn't trigger for small conversations."""
    small_messages = [
        Message("user", "Hello", datetime.now()),
        Message("assistant", "Hi there!", datetime.now()),
        Message("system", "Command executed successfully.", datetime.now()),
    ]

    # Should not trigger auto-compacting
    assert not should_auto_compact(small_messages)


def test_auto_compact_log_reduces_massive_tool_result():
    """Test that auto_compact_log properly reduces massive tool results."""
    messages = create_test_conversation()

    # Get original sizes
    original_msg = messages[2]  # The massive tool result
    model = get_default_model() or get_model("gpt-4")
    original_tokens = len_tokens(original_msg.content, model.model)
    original_chars = len(original_msg.content)

    # Verify we have a massive message to start with
    assert original_tokens > 2000, "Test message should be massive (>2000 tokens)"
    assert original_chars > 20000, "Test message should be massive (>20k chars)"

    # Apply auto-compacting
    compacted_messages = list(auto_compact_log(messages))

    # Verify structure is preserved
    assert len(compacted_messages) == 3, "Should preserve message count"
    assert compacted_messages[0].role == "user"
    assert compacted_messages[1].role == "assistant"
    assert compacted_messages[2].role == "system"

    # Verify the massive tool result was compacted
    compacted_msg = compacted_messages[2]
    compacted_tokens = len_tokens(compacted_msg.content, model.model)
    compacted_chars = len(compacted_msg.content)

    # Should be dramatically smaller
    assert compacted_chars < original_chars * 0.1, "Should reduce size by >90%"
    assert compacted_tokens < 200, "Compacted message should be under 200 tokens"

    # Should contain summary information
    assert "[Large tool output removed" in compacted_msg.content
    assert "tokens]" in compacted_msg.content
    assert "find /usr -type f" in compacted_msg.content


def test_create_tool_result_summary():
    """Test the _create_tool_result_summary helper function."""
    content = "Ran command: `ls -la`\n/usr/bin/file1.txt\n/usr/bin/file2.txt\n..."
    tokens = 1000

    summary = _create_tool_result_summary(content, tokens)

    # Should contain key information
    assert "1000 tokens" in summary
    assert "Ran command: `ls -la`" in summary
    assert "Tool execution completed" in summary
    assert "automatically removed" in summary


def test_create_tool_result_summary_with_error():
    """Test summary generation for failed tool execution."""
    content = (
        "Ran command: `invalid_command`\nError: command not found\nFailed to execute"
    )
    tokens = 500

    summary = _create_tool_result_summary(content, tokens)

    # Should detect failure
    assert "500 tokens" in summary
    assert "Tool execution failed" in summary
    assert "Ran command: `invalid_command`" in summary


def test_auto_compact_preserves_small_messages():
    """Test that auto-compacting preserves small messages unchanged."""
    small_messages = [
        Message("user", "Hello", datetime.now()),
        Message("assistant", "Hi there!", datetime.now()),
        Message("system", "Command executed successfully.", datetime.now()),
    ]

    compacted = list(auto_compact_log(small_messages))

    # Should be unchanged
    assert len(compacted) == len(small_messages)
    for original, compacted_msg in zip(small_messages, compacted):
        assert original.content == compacted_msg.content
        assert original.role == compacted_msg.role


def test_auto_compact_preserves_pinned_messages():
    """Test that pinned messages are never compacted."""
    messages = create_test_conversation()
    # Make the massive message pinned
    messages[2] = messages[2].replace(pinned=True)

    compacted = list(auto_compact_log(messages))

    # Pinned message should be preserved unchanged
    assert compacted[2].content == messages[2].content
    assert compacted[2].pinned


if __name__ == "__main__":
    # Allow running the test directly
    pytest.main([__file__])
