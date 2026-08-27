"""Tests for --track-tokens / GPTME_TRACK_TOKENS token-accumulation logic."""

from unittest.mock import MagicMock, patch

import pytest

import gptme.chat as chat_module
from gptme.chat import _log_token_usage, _reset_token_accumulator
from gptme.message import Message


@pytest.fixture(autouse=True)
def reset_accumulator():
    """Reset the session token accumulator before each test."""
    _reset_token_accumulator()
    yield
    _reset_token_accumulator()


def _make_model_meta(model_name: str = "mock/gpt-mock", context: int = 10_000):
    """Return a lightweight ModelMeta stub."""
    meta = MagicMock()
    meta.model = model_name
    meta.full = model_name
    meta.context = context
    return meta


def test_accumulator_sums_across_turns(capsys):
    """_session_tokens grows with each _log_token_usage call."""
    meta = _make_model_meta(context=10_000)

    with (
        patch("gptme.chat.get_model", return_value=meta),
        patch("gptme.chat.len_tokens", side_effect=[100, 20, 200, 30]),
    ):
        msgs1 = [Message("user", "hello")]
        resp1 = Message("assistant", "world")
        _log_token_usage(msgs1, resp1, "mock/gpt-mock")
        assert chat_module._session_tokens == 120  # 100 in + 20 out

        msgs2 = [
            Message("user", "hello"),
            Message("assistant", "world"),
            Message("user", "more"),
        ]
        resp2 = Message("assistant", "done")
        _log_token_usage(msgs2, resp2, "mock/gpt-mock")
        assert chat_module._session_tokens == 350  # 120 + 200 in + 30 out


def test_accumulator_includes_tool_result_content(capsys):
    """Token count includes tool-result messages (as system msgs) that land in msgs."""
    meta = _make_model_meta(context=10_000)

    # Tool results are injected as system messages in gptme's conversation format
    tool_result = Message("system", "```\nls output: file1 file2\n```")
    msgs = [
        Message("user", "list files"),
        Message("assistant", "ok"),
        tool_result,
    ]
    resp = Message("assistant", "done")

    with (
        patch("gptme.chat.get_model", return_value=meta),
        patch("gptme.chat.len_tokens", side_effect=[150, 10]) as mock_len_tokens,
    ):
        _log_token_usage(msgs, resp, "mock/gpt-mock")

    # len_tokens was called with the full msgs list (which includes the tool result)
    first_call_msgs = mock_len_tokens.call_args_list[0][0][0]
    assert len(first_call_msgs) == 3
    assert chat_module._session_tokens == 160


def test_output_shows_context_percentage(capsys):
    """The printed line includes percent-of-context."""
    meta = _make_model_meta(context=10_000)

    with (
        patch("gptme.chat.get_model", return_value=meta),
        patch("gptme.chat.len_tokens", side_effect=[1000, 50]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    captured = capsys.readouterr()
    # 1000 / 10_000 = 10.0%
    assert "10.0%" in captured.out or "10.0%" in captured.err


def test_reset_clears_accumulator():
    """_reset_token_accumulator brings the counter back to zero."""
    meta = _make_model_meta(context=10_000)

    with (
        patch("gptme.chat.get_model", return_value=meta),
        patch("gptme.chat.len_tokens", side_effect=[50, 10]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    assert chat_module._session_tokens == 60
    _reset_token_accumulator()
    assert chat_module._session_tokens == 0
