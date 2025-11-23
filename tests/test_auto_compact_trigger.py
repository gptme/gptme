"""Tests for Phase 3.2 auto-compact trigger functionality."""

from unittest.mock import Mock, patch

from gptme.logmanager import prepare_messages
from gptme.message import Message


def test_auto_compact_trigger_activates_above_threshold():
    """Test that auto-compact triggers when messages exceed 70% threshold."""
    # Create a mock model with small context for testing
    mock_model = Mock()
    mock_model.model = "test-model"
    mock_model.context = 1000  # Small context for easy testing

    # Create messages that will exceed 70% threshold (>700 tokens)
    # Each message ~100 tokens, so 8 messages = ~800 tokens
    msgs = [
        Message("user", "test message " * 50),  # ~100 tokens
        Message("assistant", "response " * 50),
        Message("user", "test message " * 50),
        Message("assistant", "response " * 50),
        Message("user", "test message " * 50),
        Message("assistant", "response " * 50),
        Message("user", "test message " * 50),
        Message("assistant", "response " * 50),
    ]

    with (
        patch("gptme.llm.models.get_default_model", return_value=mock_model),
        patch("gptme.message.len_tokens") as mock_len_tokens,
        patch("gptme.tools.autocompact.auto_compact_log") as mock_auto_compact,
    ):
        # Mock token counting
        # First call (before compact): 800 tokens (exceeds 700 threshold)
        # Second call (after compact): 600 tokens
        mock_len_tokens.side_effect = [800, 600, 800, 600]

        # Mock auto_compact_log to return compacted messages
        mock_auto_compact.return_value = msgs[:6]  # Return fewer messages

        # Call prepare_messages
        prepare_messages(msgs)

        # Verify auto_compact_log was called
        assert mock_auto_compact.called
        mock_auto_compact.assert_called_once()

        # Verify it was called with correct limit (80% of context = 800)
        call_args = mock_auto_compact.call_args
        assert call_args[1]["limit"] == 800


def test_auto_compact_does_not_trigger_below_threshold():
    """Test that auto-compact doesn't trigger when below threshold."""
    # Create a mock model
    mock_model = Mock()
    mock_model.model = "test-model"
    mock_model.context = 10000  # Large context

    # Create few messages (below threshold)
    msgs = [
        Message("user", "test"),
        Message("assistant", "response"),
    ]

    with (
        patch("gptme.llm.models.get_default_model", return_value=mock_model),
        patch("gptme.message.len_tokens", return_value=500),
        patch("gptme.tools.autocompact.auto_compact_log") as mock_auto_compact,
    ):
        # Call prepare_messages
        prepare_messages(msgs)

        # Verify auto_compact_log was NOT called (500 < 7000 threshold)
        assert not mock_auto_compact.called
