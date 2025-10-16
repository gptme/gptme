"""
Tests for lesson system mode detection.
"""

from gptme.message import Message
from gptme.tools.lessons import (
    _detect_conversation_mode,
    _extract_keywords_for_mode,
)


def test_detect_interactive_mode():
    """Test detection of interactive conversation mode."""
    # Regular back-and-forth conversation (50% user messages)
    log = [
        Message("user", "How do I use patch?"),
        Message("assistant", "The patch tool..."),
        Message("user", "What about shell commands?"),
        Message("assistant", "Shell commands..."),
    ]

    mode = _detect_conversation_mode(log)
    assert mode == "interactive"


def test_detect_autonomous_mode():
    """Test detection of autonomous conversation mode."""
    # Autonomous mode: few user messages (< 30%)
    log = [
        Message("user", "You are Bob, running autonomously..."),
        Message("assistant", "I'll start checking for loose ends..."),
        Message("assistant", "Let me check GitHub notifications..."),
        Message("assistant", "Now I'll select a forward-moving task..."),
        Message("assistant", "Working on implement-feature task..."),
        Message("assistant", "Creating a PR for the changes..."),
        Message("system", "Execution complete"),
    ]

    mode = _detect_conversation_mode(log)
    assert mode == "autonomous"


def test_detect_mode_edge_cases():
    """Test edge cases for mode detection."""
    # Empty log -> default to interactive
    assert _detect_conversation_mode([]) == "interactive"

    # Single user message -> interactive
    log = [Message("user", "Hello")]
    assert _detect_conversation_mode(log) == "interactive"

    # Only system messages -> interactive
    log = [Message("system", "System message")]
    assert _detect_conversation_mode(log) == "interactive"


def test_extract_keywords_interactive_mode():
    """Test keyword extraction in interactive mode."""
    log = [
        Message("user", "How do I create a patch for changes?"),
        Message("assistant", "You can use the patch tool..."),
        Message("user", "What about testing the patch?"),
    ]

    # Interactive mode: only extract from user messages
    keywords = _extract_keywords_for_mode(log, "interactive")

    # Should contain keywords from user messages only
    assert "patch" in keywords
    assert "create" in keywords or "changes" in keywords
    assert "testing" in keywords

    # Should not contain keywords unique to assistant message
    # (assuming "tool" appears only in assistant message)


def test_extract_keywords_autonomous_mode():
    """Test keyword extraction in autonomous mode."""
    log = [
        Message("user", "You are Bob, running autonomously..."),
        Message("assistant", "I'll check GitHub notifications first..."),
        Message("assistant", "Now working on implementing feature X..."),
    ]

    # Autonomous mode: extract from both user and assistant messages
    keywords = _extract_keywords_for_mode(log, "autonomous")

    # Should contain keywords from both user and assistant messages
    assert "autonomously" in keywords or "autonomous" in keywords
    assert "github" in keywords
    assert "implementing" in keywords or "feature" in keywords


def test_extract_keywords_filters():
    """Test that keyword extraction filters appropriately."""
    log = [
        Message("user", "I would like to know about these things"),
    ]

    keywords = _extract_keywords_for_mode(log, "interactive")

    # Should filter out common words
    assert "would" not in keywords
    assert "could" not in keywords
    assert "about" not in keywords
    assert "these" not in keywords

    # Should keep meaningful words
    assert "things" in keywords or "know" in keywords


def test_extract_keywords_limit():
    """Test that keyword extraction respects limits."""
    # Create a long message with many keywords
    long_content = " ".join([f"keyword{i}" for i in range(100)])
    log = [Message("user", long_content)]

    keywords = _extract_keywords_for_mode(log, "interactive")

    # Should limit to 20 keywords
    assert len(keywords) <= 20
