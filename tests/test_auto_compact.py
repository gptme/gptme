"""
Tests for auto-compacting functionality that handles conversations with massive tool results.
"""

from datetime import datetime

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
    from gptme.llm.models import get_default_model, get_model

    model = get_default_model() or get_model("gpt-4")
    large_output = "x" * 100000
    original_tokens = len_tokens(large_output, model.model)
    summary = create_tool_result_summary(large_output, original_tokens, None)

    # Should create a compact summary
    assert len(summary) < 500, "Summary should be much smaller than original"
    assert "[Large tool output removed" in summary


def test_phase3_extractive_compression():
    """Test Phase 3 extractive compression for long messages."""
    model = get_default_model() or get_model("gpt-4")

    # Create a conversation that triggers auto-compacting (85% of context)
    # Start with the base massive conversation
    messages = create_test_conversation()

    # Add a long assistant message that Phase 3 will compress
    long_content = """
# Technical Documentation

## Overview
This is a comprehensive guide covering multiple aspects of the system architecture.

## Background Context
The system was designed to handle large-scale data processing with emphasis on reliability.
We need to ensure that all components work together seamlessly for optimal performance.
The architecture follows best practices from industry leaders in distributed systems.

## Implementation Details
The core implementation consists of several modules working in coordination.
Each module has specific responsibilities and interfaces with other components.
Performance optimization was a key consideration throughout the development process.
"""
    # Repeat content to reach >1000 tokens
    long_content = long_content * 30  # ~1500 tokens

    # Insert long assistant message before the massive tool result
    messages.insert(1, Message("assistant", long_content, datetime.now()))

    # Get original size (the long assistant message is now at index 1)
    original_msg = messages[1]
    original_tokens = len_tokens(original_msg.content, model.model)

    # Verify message is long enough to trigger Phase 3
    assert (
        original_tokens > 1000
    ), f"Test message should be >1000 tokens, got {original_tokens}"

    # Verify conversation triggers auto-compacting
    assert should_auto_compact(
        messages
    ), "Test conversation should trigger auto-compacting"

    # Apply auto-compacting (should trigger Phase 3)
    compacted_messages = list(auto_compact_log(messages))

    # Verify structure preserved (4 messages: user, assistant[long], assistant, system[massive])
    assert (
        len(compacted_messages) == 4
    ), f"Should preserve message count, got {len(compacted_messages)}"

    # Find the long assistant message (should still be at index 1 after Phase 2)
    # Phase 2 compacts the massive system message (index 3), not the assistant message
    compacted_msg = compacted_messages[1]
    compacted_tokens = len_tokens(compacted_msg.content, model.model)

    # Phase 3 should reduce tokens (target: 30% reduction, retain 70%)
    # We expect some reduction, but content should still be readable
    assert (
        compacted_tokens < original_tokens
    ), f"Phase 3 should compress: {original_tokens} -> {compacted_tokens}"
    assert compacted_tokens > original_tokens * 0.5, "Should retain >50% of content"

    # Verify headings are preserved (Phase 3 config: preserve_headings=True)
    assert (
        "# Technical Documentation" in compacted_msg.content
        or "## " in compacted_msg.content
    ), "Should preserve markdown headings"


def test_phase3_preserves_code_blocks():
    """Test that Phase 3 preserves code blocks during compression."""
    # Create long message with code blocks
    long_content = (
        """
# Code Example

Here's how to implement the feature:

```python
def process_data(input_data):
    result = []
    for item in input_data:
        processed = transform(item)
        result.append(processed)
    return result
```

Additional context and explanation about the implementation details.
We need to ensure proper error handling and edge case coverage.
"""
        * 20
    )  # Repeat to reach >1000 tokens

    messages = [
        Message("user", "Show me the code", datetime.now()),
        Message("assistant", long_content, datetime.now()),
    ]

    # Apply auto-compacting
    compacted_messages = list(auto_compact_log(messages))

    compacted_msg = compacted_messages[1]

    # Verify code blocks are preserved (Phase 3 config: preserve_code=True)
    assert "```python" in compacted_msg.content, "Should preserve code block markers"
    assert "def process_data" in compacted_msg.content, "Should preserve code content"


def test_get_compacted_name():
    """Test the _get_compacted_name helper function."""
    assert _get_compacted_name("gptme_1234") == "gptme_1234_compact"
    assert _get_compacted_name("session_abc") == "session_abc_compact"
