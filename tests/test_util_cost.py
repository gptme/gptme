"""Tests for gptme.util.cost — token counting and cost calculation."""

from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.util.cost import _cost, _tokens_inout

# ──────────────────────────────────────────────
# _tokens_inout
# ──────────────────────────────────────────────


class TestTokensInOut:
    def test_empty_messages(self):
        assert _tokens_inout([]) == (0, 0)

    def test_single_user_message(self):
        """Single user message → all input tokens, zero output.

        len_tokens is called twice: once for msgs[:-1]=[] (returns 0),
        once for msgs[-1] (the user message, returns 5).
        Since role != assistant, both go to input.
        """
        msgs = [Message(role="user", content="hello")]
        mock_model = MagicMock(model="test-model")
        with (
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.util.cost.len_tokens", side_effect=[0, 5]),
        ):
            tok_in, tok_out = _tokens_inout(msgs)
        assert tok_in == 5
        assert tok_out == 0

    def test_user_then_assistant(self):
        """User + assistant → user=input, assistant=output."""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        mock_model = MagicMock(model="test-model")
        with (
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.util.cost.len_tokens", side_effect=[10, 5]),
        ):
            tok_in, tok_out = _tokens_inout(msgs)
        assert tok_in == 10
        assert tok_out == 5

    def test_multiple_messages_ending_user(self):
        """When last message is user, all tokens are input."""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
            Message(role="user", content="more"),
        ]
        mock_model = MagicMock(model="test-model")
        with (
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.util.cost.len_tokens", side_effect=[20, 3]),
        ):
            tok_in, tok_out = _tokens_inout(msgs)
        assert tok_in == 23  # 20 + 3
        assert tok_out == 0

    def test_multiple_messages_ending_assistant(self):
        """When last message is assistant, it counts as output."""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="response"),
        ]
        mock_model = MagicMock(model="test-model")
        with (
            patch("gptme.llm.models.get_default_model", return_value=mock_model),
            patch("gptme.util.cost.len_tokens", side_effect=[8, 12]),
        ):
            tok_in, tok_out = _tokens_inout(msgs)
        assert tok_in == 8
        assert tok_out == 12

    def test_no_model_raises(self):
        """If no model is loaded, should raise AssertionError."""
        msgs = [Message(role="user", content="hello")]
        with (
            patch("gptme.llm.models.get_default_model", return_value=None),
            pytest.raises(AssertionError, match="No model loaded"),
        ):
            _tokens_inout(msgs)


# ──────────────────────────────────────────────
# _cost
# ──────────────────────────────────────────────


class TestCost:
    def test_empty_messages(self):
        assert _cost([]) == 0.0

    def test_single_user_message(self):
        msg = Message(role="user", content="hello")
        with patch.object(Message, "cost", return_value=0.01):
            result = _cost([msg])
        assert result == pytest.approx(0.01)

    def test_user_then_assistant(self):
        """Last message (assistant) gets cost(output=True), all others get cost()."""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="response"),
        ]
        with patch.object(Message, "cost", side_effect=[0.01, 0.05]):
            result = _cost(msgs)
        assert result == pytest.approx(0.06)

    def test_multiple_turns(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
            Message(role="assistant", content="a2"),
        ]
        with patch.object(Message, "cost", side_effect=[0.01, 0.02, 0.01, 0.03]):
            result = _cost(msgs)
        assert result == pytest.approx(0.07)

    def test_single_assistant_message(self):
        """Edge: single assistant message → cost(output=True)."""
        msg = Message(role="assistant", content="hello")
        with patch.object(Message, "cost", return_value=0.02):
            result = _cost([msg])
        assert result == pytest.approx(0.02)

    def test_zero_cost_model(self):
        """Models without pricing return 0."""
        msgs = [
            Message(role="user", content="q"),
            Message(role="assistant", content="a"),
        ]
        with patch.object(Message, "cost", return_value=0.0):
            result = _cost(msgs)
        assert result == 0.0
