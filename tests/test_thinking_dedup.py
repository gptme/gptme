"""Tests for thinking block deduplication."""

from gptme.llm import _dedup_thinking_blocks


def test_no_thinking_blocks():
    """No thinking blocks - output unchanged."""
    text = "Hello, world!"
    assert _dedup_thinking_blocks(text) == text


def test_single_thinking_block():
    """Single thinking block - output unchanged."""
    text = "<think>\nHello\n</think>\n\nSome output."
    assert _dedup_thinking_blocks(text) == text


def test_consecutive_duplicate_think_blocks():
    """Consecutive identical <think> blocks should be collapsed to one."""
    text = (
        "<think>\nGood! Now let me push.\n</think>\n\n"
        "<think>\nGood! Now let me push.\n</think>\n\n"
        "<think>\nGood! Now let me push.\n</think>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    assert result.count("<think>") == 1
    assert result.count("</think>") == 1
    assert "Good! Now let me push." in result


def test_consecutive_duplicate_thinking_blocks():
    """Consecutive identical <thinking> blocks should be collapsed to one."""
    text = (
        "<thinking>\nLet me check.\n</thinking>\n\n"
        "<thinking>\nLet me check.\n</thinking>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    assert result.count("<thinking>") == 1
    assert result.count("</thinking>") == 1


def test_different_thinking_blocks_preserved():
    """Different consecutive thinking blocks should all be preserved."""
    text = (
        "<think>\nFirst thought.\n</think>\n\n" "<think>\nSecond thought.\n</think>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    assert result.count("<think>") == 2
    assert "First thought." in result
    assert "Second thought." in result


def test_many_duplicates_from_kimi():
    """Reproduce the exact Kimi K2.5 issue from #1234."""
    text = (
        "<think>\nGood! Now let me push and check the CI.\n</think>\n\n"
        "<think>\nGood! Now let me push and check the CI.\n</think>\n\n"
        "<think>\nGood, now let me commit and push this fix.\n</think>\n\n"
        "<think>\nGood! Now let me commit and push this fix.\n</think>\n\n"
        "<think>\nGood! Now let me commit and push this fix.\n</think>\n\n"
        "<think>\nGood! Now let me commit and push this fix.\n</think>\n\n"
        "<think>\nGood! Now let me commit and push this fix.\n</think>\n\n"
        "<think>\nGood! Now let me commit and push this fix.\n</think>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    # First block is unique
    assert "Good! Now let me push and check the CI." in result
    # Second unique thought
    assert "Good, now let me commit and push this fix." in result
    # Third unique thought (differs only in "Good!" vs "Good,")
    assert "Good! Now let me commit and push this fix." in result
    # Should have at most 3 <think> blocks (3 unique contents)
    assert result.count("<think>") == 3


def test_thinking_followed_by_content():
    """Thinking blocks followed by real content - content preserved."""
    text = (
        "<think>\nPlanning step.\n</think>\n\n"
        "<think>\nPlanning step.\n</think>\n\n"
        "Here is the actual response."
    )
    result = _dedup_thinking_blocks(text)
    assert result.count("<think>") == 1
    assert "Here is the actual response." in result


def test_whitespace_variations():
    """Blocks with different whitespace but same content are still deduped."""
    text = (
        "<think>\n  Let me check.  \n</think>\n\n"
        "<think>\nLet me check.\n</think>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    assert result.count("<think>") == 1


def test_non_consecutive_duplicates_preserved():
    """Non-consecutive duplicate blocks should be preserved (different context)."""
    text = (
        "<think>\nSame thought.\n</think>\n\n"
        "Some content in between.\n\n"
        "<think>\nSame thought.\n</think>\n\n"
    )
    result = _dedup_thinking_blocks(text)
    # Both should be preserved since they're not consecutive
    assert result.count("<think>") == 2
