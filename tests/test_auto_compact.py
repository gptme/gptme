"""
Tests for auto-compacting functionality that handles conversations with massive tool results.
"""

import time
from datetime import datetime

import pytest

from gptme.llm.models import get_default_model, get_model
from gptme.message import Message, len_tokens
from gptme.tools.autocompact import (
    _get_compacted_name,
    auto_compact_log,
    should_auto_compact,
)
from gptme.util.output_storage import create_tool_result_summary


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
    """Test the create_tool_result_summary helper function."""
    from datetime import datetime

    from gptme.message import Message

    content = "Ran command: `ls -la`\n/usr/bin/file1.txt\n/usr/bin/file2.txt\n..."
    tokens = 1000
    msg = Message("system", content, timestamp=datetime.now())

    summary = create_tool_result_summary(msg.content, tokens, None, "autocompact")

    # Should contain key information
    assert "1000 tokens" in summary
    assert "Ran command: `ls -la`" in summary


def test_create_tool_result_summary_with_error():
    """Test summary generation for failed tool execution."""
    from datetime import datetime

    from gptme.message import Message

    content = (
        "Ran command: `invalid_command`\nError: command not found\nFailed to execute"
    )
    tokens = 500
    msg = Message("system", content, timestamp=datetime.now())

    summary = create_tool_result_summary(msg.content, tokens, None, "autocompact")

    # Should detect failure
    assert "500 tokens" in summary
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


def test_get_compacted_name_no_suffix():
    """Test compacted name generation for conversation without existing suffix."""
    import re

    name = _get_compacted_name("2025-10-13-flying-yellow-alien")
    # Should match: base-name-before-compact-XXXX where XXXX is timestamp suffix YYYYMMDD-HHMMSS
    assert re.match(r"^2025-10-13-flying-yellow-alien-compacted-\d{8}-\d{6}$", name)


def test_get_compacted_name_one_suffix():
    """Test compacted name generation when suffix already exists (gets new unique suffix)."""
    import re

    name = _get_compacted_name(
        "2025-10-13-flying-yellow-alien-compacted-20251029-100000"
    )
    # Should strip old suffix and add new one with timestamp
    assert re.match(r"^2025-10-13-flying-yellow-alien-compacted-\d{8}-\d{6}$", name)


def test_get_compacted_name_multiple_suffixes():
    """Test compacted name generation with multiple accumulated suffixes (should strip all)."""
    import re

    name = _get_compacted_name(
        "2025-10-13-flying-yellow-alien-compacted-20251028-120000-compacted-20251029-090000"
    )
    assert re.match(r"^2025-10-13-flying-yellow-alien-compacted-\d{8}-\d{6}$", name)


def test_get_compacted_name_edge_cases():
    """Test compacted name generation with various edge cases."""
    import re

    # Short name
    name = _get_compacted_name("conv")
    assert re.match(r"^conv-compacted-\d{8}-\d{6}$", name)

    # Name containing 'compact' but not as suffix
    name = _get_compacted_name("compact-test")
    assert re.match(r"^compact-test-compacted-\d{8}-\d{6}$", name)

    # Name ending with similar but different suffix
    name = _get_compacted_name("test-before-compaction")
    assert re.match(r"^test-before-compaction-compacted-\d{8}-\d{6}$", name)


def test_get_compacted_name_uniqueness():
    """Test that multiple calls produce unique compacted names."""
    name1 = _get_compacted_name("my-conversation")
    time.sleep(1.1)  # Ensure unique timestamps (second-level resolution)
    name2 = _get_compacted_name("my-conversation")
    time.sleep(1.1)
    name3 = _get_compacted_name("my-conversation")

    # All should have the same base but different timestamps
    assert name1 != name2
    assert name2 != name3
    assert name1 != name3

    # All should start with the same base
    assert name1.startswith("my-conversation-compacted-")
    assert name2.startswith("my-conversation-compacted-")
    assert name3.startswith("my-conversation-compacted-")


def test_get_compacted_name_with_hex_suffix():
    """Test that compacted names with timestamp suffixes are correctly stripped.

    This is a regression test for the bug where:
    "my-conversation-compacted-20251028-120000" would become
    "my-conversation-compacted-20251028-120000-compacted-20251029-100000"
    instead of
    "my-conversation-compacted-20251029-100000"
    """
    import re

    # Test with a compacted name that has a valid timestamp suffix
    name = _get_compacted_name("my-conversation-compacted-20251028-120000")
    # Should strip the old suffix and add a new one
    assert re.match(r"^my-conversation-compacted-\d{8}-\d{6}$", name)
    # Should NOT contain the old timestamp
    assert "20251028-120000" not in name


def test_get_compacted_name_empty_string():
    """Test compacted name generation with empty string raises ValueError."""
    with pytest.raises(ValueError, match="conversation name cannot be empty"):
        _get_compacted_name("")


def test_strip_reasoning_removes_think_tags():
    """Test that strip_reasoning removes <think> tags."""
    from gptme.context import strip_reasoning

    content = "Before <think>This is reasoning</think> After"
    stripped, tokens_saved = strip_reasoning(content, "gpt-4")

    assert "<think>" not in stripped
    assert "</think>" not in stripped
    assert "Before" in stripped
    assert "After" in stripped
    assert tokens_saved > 0


def test_strip_reasoning_removes_thinking_tags():
    """Test that strip_reasoning removes <thinking> tags."""
    from gptme.context import strip_reasoning

    content = "Before <thinking>This is reasoning</thinking> After"
    stripped, tokens_saved = strip_reasoning(content, "gpt-4")

    assert "<thinking>" not in stripped
    assert "</thinking>" not in stripped
    assert "Before" in stripped
    assert "After" in stripped
    assert tokens_saved > 0


def test_strip_reasoning_handles_multiple_blocks():
    """Test that strip_reasoning removes multiple reasoning blocks."""
    from gptme.context import strip_reasoning

    content = "<think>First</think> Middle <thinking>Second</thinking> End"
    stripped, tokens_saved = strip_reasoning(content, "gpt-4")

    assert "<think>" not in stripped
    assert "<thinking>" not in stripped
    assert "Middle" in stripped
    assert "End" in stripped
    assert tokens_saved > 0


def test_strip_reasoning_preserves_content_without_tags():
    """Test that strip_reasoning preserves content without reasoning tags."""
    from gptme.context import strip_reasoning

    content = "This is normal content without reasoning"
    stripped, tokens_saved = strip_reasoning(content, "gpt-4")

    assert stripped == content
    assert tokens_saved == 0


def test_auto_compact_strips_reasoning_from_older_messages():
    """Test that auto_compact_log strips reasoning from older messages."""
    messages = [
        Message("user", "First <think>old reasoning</think>", datetime.now()),
        Message("assistant", "Second <think>old reasoning</think>", datetime.now()),
        Message("user", "Third <think>old reasoning</think>", datetime.now()),
        Message("assistant", "Fourth <think>old reasoning</think>", datetime.now()),
        Message("user", "Fifth <think>old reasoning</think>", datetime.now()),
        Message("assistant", "Recent <think>recent reasoning</think>", datetime.now()),
        Message("user", "Most recent <think>recent reasoning</think>", datetime.now()),
    ]

    # Apply auto-compacting with reasoning_strip_age_threshold=5
    compacted = list(auto_compact_log(messages, reasoning_strip_age_threshold=5))

    # First two messages (distance from end >= 5) should have reasoning stripped
    assert "<think>" not in compacted[0].content
    assert "<think>" not in compacted[1].content

    # Last 5 messages (distance from end < 5) should keep reasoning
    for i in range(-5, 0):
        # Check if original had <think>, if so, compacted should too
        if "<think>" in messages[i].content:
            assert "<think>" in compacted[i].content


def test_auto_compact_reasoning_strip_threshold_zero():
    """Test that threshold=0 strips reasoning from all messages."""
    messages = [
        Message("user", "Message 1 <think>reasoning 1</think>", datetime.now()),
        Message("assistant", "Message 2 <think>reasoning 2</think>", datetime.now()),
        Message("user", "Message 3 <think>reasoning 3</think>", datetime.now()),
    ]

    # Apply with threshold=0 (strip all)
    compacted = list(auto_compact_log(messages, reasoning_strip_age_threshold=0))

    # All messages should have reasoning stripped
    for msg in compacted:
        assert "<think>" not in msg.content


if __name__ == "__main__":
    # Allow running the test directly
    pytest.main([__file__])


def test_extract_code_blocks():
    """Test code block extraction preserves code."""
    from gptme.tools.autocompact import extract_code_blocks

    content = """Here is some text.
```python
def hello():
    print("world")
```
More text after.
```bash
echo "test"
```
Final text."""

    cleaned, blocks = extract_code_blocks(content)

    # Should have 2 code blocks
    assert len(blocks) == 2

    # Cleaned content should have markers
    assert "__CODE_BLOCK_0__" in cleaned
    assert "__CODE_BLOCK_1__" in cleaned

    # Original code blocks preserved
    assert "def hello():" in blocks[0][1]
    assert 'echo "test"' in blocks[1][1]


def test_score_sentence():
    """Test sentence scoring heuristics."""
    from gptme.tools.autocompact import score_sentence

    # First sentence should score higher
    score_first = score_sentence("This is the first sentence.", 0, 5)
    score_middle = score_sentence("This is a middle sentence.", 2, 5)
    assert score_first > score_middle

    # Last sentence should score higher than middle
    score_last = score_sentence("This is the last sentence.", 4, 5)
    assert score_last > score_middle

    # Key terms increase score
    score_with_key = score_sentence("This contains an error message.", 2, 5)
    score_without_key = score_sentence("This is a normal sentence.", 2, 5)
    assert score_with_key > score_without_key


def test_compress_content():
    """Test content compression preserves code and important content."""
    from gptme.tools.autocompact import compress_content

    content = """First important sentence. This is filler text that can be removed.
Another filler sentence. This has an error we should keep.
```python
def critical_code():
    return "must preserve"
```
More filler text here. Final important conclusion."""

    compressed = compress_content(content, target_ratio=0.7)

    # Code block must be preserved
    assert "def critical_code():" in compressed
    assert 'return "must preserve"' in compressed

    # Important sentences should be kept (first, error, last)
    assert "First important sentence" in compressed or "error" in compressed

    # Should be shorter than original
    assert len(compressed) < len(content)


def test_auto_compact_phase3_compresses_long_messages():
    """Test Phase 3 extractive compression for long assistant messages."""
    from gptme.message import Message
    from gptme.tools.autocompact import auto_compact_log

    # Create a long assistant message (>1000 tokens worth of content)
    long_content = "This is a sentence. " * 200  # ~600 words = ~800 tokens
    long_content += "\n```python\ndef important(): pass\n```\n"
    long_content += "Final conclusion sentence. " * 50  # More padding

    messages = [
        Message("user", "Hello"),
        Message("assistant", "Short response"),
        Message("user", "Tell me more"),
        Message("assistant", long_content),  # This should be compressed (distance=3)
        Message("user", "Thanks"),
        Message("assistant", "You're welcome"),
        Message("user", "One more thing"),
    ]

    compacted = list(auto_compact_log(messages, limit=100000))

    # Should have same number of messages
    assert len(compacted) == len(messages)

    # The long message should be compressed
    long_msg_idx = 3
    original_length = len(messages[long_msg_idx].content)
    compacted_length = len(compacted[long_msg_idx].content)

    # Should be shorter
    assert compacted_length < original_length

    # Code block should still be present
    assert "def important():" in compacted[long_msg_idx].content


def test_estimate_compaction_savings():
    """Test that estimate_compaction_savings correctly estimates potential savings."""
    from gptme.tools.autocompact import estimate_compaction_savings

    # Create messages with known characteristics
    messages = [
        Message("system", "System message"),
        Message("user", "Short user message"),
        Message(
            "assistant",
            "<think>This is reasoning content that should be stripped.</think>\nThis is the actual response.",
        ),
        Message("system", "Another system message"),
    ]

    total, estimated_savings, reasoning_savings = estimate_compaction_savings(
        messages, reasoning_strip_age_threshold=2
    )

    # Should detect some potential savings from reasoning stripping
    assert total > 0
    assert estimated_savings >= 0
    assert reasoning_savings >= 0


def test_estimate_compaction_savings_tool_results_only_when_over_limit():
    """Test that tool result savings are only counted when over/close to limit.

    This tests the fix for Greptile review finding: estimation must match
    actual compaction logic, which only removes tool results when
    tokens > limit or close_to_limit.
    """
    from gptme.tools.autocompact import estimate_compaction_savings

    # Create message with massive tool result (>2000 tokens)
    massive_content = "x " * 3000  # ~3000 tokens
    messages = [
        Message("user", "Request"),
        Message("system", massive_content),  # Massive tool result
        Message("assistant", "Response"),
    ]

    # With a very high limit (not close to it), tool results should NOT be counted
    # because actual compaction wouldn't remove them
    total_high, savings_high, _ = estimate_compaction_savings(
        messages,
        limit=1000000,  # Very high limit
    )

    # With a low limit (definitely over it), tool results SHOULD be counted
    total_low, savings_low, _ = estimate_compaction_savings(
        messages,
        limit=100,  # Very low limit - definitely over
    )

    # Savings should be higher when over limit (tool results counted)
    assert savings_low > savings_high, (
        f"Savings should be higher when over limit: low_limit={savings_low}, "
        f"high_limit={savings_high}"
    )


def test_estimate_compaction_savings_includes_phase3():
    """Test that estimation includes Phase 3 assistant message compression.

    This tests the fix for Greptile review finding: estimation was missing
    Phase 3 which compresses long assistant messages.
    """
    from gptme.tools.autocompact import estimate_compaction_savings

    # Create conversation with long assistant message (>1000 tokens)
    long_assistant_content = "word " * 1500  # ~1500 tokens
    messages = [
        Message("user", "Request 1"),
        Message("assistant", long_assistant_content),  # Long, will be compressed
        Message("user", "Request 2"),
        Message("assistant", "Short response"),  # Recent, won't be compressed
        Message("user", "Request 3"),
        Message("assistant", "Final response"),  # Recent, won't be compressed
    ]

    # With low limit (over it), Phase 3 compression should be estimated
    total, estimated_savings, reasoning_savings = estimate_compaction_savings(
        messages,
        limit=100,  # Over limit to trigger Phase 3 estimation
        assistant_compression_age_threshold=2,  # Compress messages 2+ from end
    )

    # Should have savings from assistant compression (not just reasoning)
    # Since we have no reasoning tags, savings should come from compression
    assert (
        estimated_savings > 0
    ), f"Expected compression savings for long assistant message, got {estimated_savings}"


def test_should_auto_compact_respects_minimum_savings():
    """Test that should_auto_compact skips when estimated savings are too low.

    This tests the fix for Issue #945 where 3.8% savings wasn't worth
    the cost of prompt cache invalidation.
    """
    from gptme.tools.autocompact import should_auto_compact

    # Create messages that are close to limit but have minimal compaction potential
    # Small messages with no reasoning tags and no massive tool results
    messages = [Message("user", f"Short message {i}") for i in range(100)]

    # Even if close to limit, should not trigger if savings would be minimal
    result = should_auto_compact(messages, limit=500)  # Low limit to trigger check

    # With minimal savings potential (no reasoning, no tool results, no long messages),
    # should return False even though we're "over limit"
    assert (
        result is False
    ), "should_auto_compact should return False when savings are below threshold"


def test_should_auto_compact_triggers_with_high_savings():
    """Test that should_auto_compact triggers when savings are substantial."""
    from gptme.tools.autocompact import should_auto_compact

    # Create messages with high savings potential
    # Include reasoning content and massive tool result
    messages = [
        Message("user", "Initial request"),
        Message(
            "assistant",
            "<think>" + "reasoning " * 500 + "</think>\nResponse",
        ),
        Message("system", "tool result " * 3000),  # Massive tool result
        Message("user", "Follow up"),
        Message("assistant", "Final response"),
    ]

    # Should trigger because:
    # 1. Massive tool result = high savings potential
    # 2. Reasoning tags = additional savings
    # 3. Over the low limit
    result = should_auto_compact(messages, limit=100)

    assert (
        result is True
    ), "should_auto_compact should return True when savings exceed threshold"


def test_compact_resume_error_handling():
    """Test that _compact_resume provides useful error messages when LLM fails."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.autocompact import _compact_resume

    # Create a mock context with a mock log manager
    mock_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.manager = mock_manager

    # Create enough messages to pass the minimum check
    messages = [
        Message("system", "System prompt"),
        Message("user", "User message 1"),
        Message("assistant", "Assistant response 1"),
        Message("user", "User message 2"),
        Message("assistant", "Assistant response 2"),
    ]

    # Mock the LLM to raise an exception with an empty message
    with patch("gptme.tools.autocompact.llm") as mock_llm:
        # Exception with empty string (the bug we're fixing)
        mock_llm.reply.side_effect = Exception("")

        results = list(_compact_resume(mock_ctx, messages))

        # Should have the progress message and error message
        assert len(results) >= 2
        error_msg = results[-1]
        assert error_msg.role == "system"
        assert "Failed to generate resume" in error_msg.content
        # Should include exception type when message is empty
        assert "Exception" in error_msg.content


def test_compact_resume_error_with_message():
    """Test that _compact_resume shows actual error message when provided."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.autocompact import _compact_resume

    mock_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.manager = mock_manager

    messages = [
        Message("system", "System prompt"),
        Message("user", "User message 1"),
        Message("assistant", "Assistant response 1"),
        Message("user", "User message 2"),
        Message("assistant", "Assistant response 2"),
    ]

    with patch("gptme.tools.autocompact.llm") as mock_llm:
        # Exception with actual message
        mock_llm.reply.side_effect = Exception("API rate limit exceeded")

        results = list(_compact_resume(mock_ctx, messages))

        error_msg = results[-1]
        assert "API rate limit exceeded" in error_msg.content


def test_compact_resume_no_model():
    """Test that _compact_resume handles missing model gracefully."""
    from unittest.mock import MagicMock, patch

    from gptme.tools.autocompact import _compact_resume

    mock_manager = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.manager = mock_manager

    messages = [
        Message("system", "System prompt"),
        Message("user", "User message 1"),
        Message("assistant", "Assistant response 1"),
        Message("user", "User message 2"),
        Message("assistant", "Assistant response 2"),
    ]

    with patch("gptme.tools.autocompact.get_default_model") as mock_get_model:
        # No model configured
        mock_get_model.return_value = None

        results = list(_compact_resume(mock_ctx, messages))

        # Should have progress message and error about missing model
        assert len(results) >= 2
        error_msg = results[-1]
        assert error_msg.role == "system"
        assert "No default model configured" in error_msg.content
