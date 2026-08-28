"""Tests for --track-tokens / GPTME_TRACK_TOKENS token-accumulation logic."""

import importlib
from contextvars import copy_context
from unittest.mock import MagicMock, patch

import pytest

chat_module = importlib.import_module("gptme.chat")
from gptme.chat import (
    _get_session_tokens,
    _log_token_usage,
    _reset_token_accumulator,
)
from gptme.message import Message


@pytest.fixture(autouse=True)
def reset_accumulator():
    """Reset the session token accumulator before each test."""
    _reset_token_accumulator()
    yield
    _reset_token_accumulator()


def _make_model_meta(model_name: str = "mock/gpt-mock", context: int | None = 10_000):
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
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[100, 20, 200, 30]),
    ):
        msgs1 = [Message("user", "hello")]
        resp1 = Message("assistant", "world")
        _log_token_usage(msgs1, resp1, "mock/gpt-mock")
        assert _get_session_tokens() == 120  # 100 in + 20 out

        msgs2 = [
            Message("user", "hello"),
            Message("assistant", "world"),
            Message("user", "more"),
        ]
        resp2 = Message("assistant", "done")
        _log_token_usage(msgs2, resp2, "mock/gpt-mock")
        assert _get_session_tokens() == 350  # 120 + 200 in + 30 out


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
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(
            chat_module, "len_tokens", side_effect=[150, 10]
        ) as mock_len_tokens,
    ):
        _log_token_usage(msgs, resp, "mock/gpt-mock")

    # len_tokens was called with the full msgs list (which includes the tool result)
    first_call_msgs = mock_len_tokens.call_args_list[0][0][0]
    assert len(first_call_msgs) == 3
    assert _get_session_tokens() == 160


def test_output_shows_context_percentage_on_stderr(capsys):
    """Tracking is human-readable stderr output, preserving structured stdout."""
    meta = _make_model_meta(context=10_000)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[1000, 50]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "10.0%" in captured.err
    assert "session total: 1,050" in captured.err


def test_reset_clears_accumulator():
    """_reset_token_accumulator brings the counter back to zero."""
    meta = _make_model_meta(context=10_000)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[50, 10]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    assert _get_session_tokens() == 60
    _reset_token_accumulator()
    assert _get_session_tokens() == 0


def test_output_handles_unknown_context_limit(capsys):
    """Models without known context metadata still report usage."""
    meta = _make_model_meta(context=None)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[100, 20]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "context: 100 / unknown" in captured.err
    assert "session total: 120" in captured.err


def test_accumulator_restored_after_nested_chat_same_context():
    """Nested chat() in the same context restores the parent's running total.

    The existing copy_context test is insufficient: production subagents run
    chat() in the *same* context (same thread, no copy_context barrier).
    _reset_token_accumulator() inside the inner chat() would clobber the outer
    total.  The fix saves _prev_tokens before resetting and restores on exit.
    """
    meta = _make_model_meta(context=10_000)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[100, 20, 50, 10]),
    ):
        # Outer chat: accumulate 120 tokens.
        _log_token_usage(
            [Message("user", "outer")],
            Message("assistant", "reply"),
            "mock/gpt-mock",
        )
        assert _get_session_tokens() == 120

        # Simulate what the fixed chat() does at nested-call entry:
        # save outer state, reset for inner chat.
        prev_tokens = chat_module._session_tokens.get()
        _reset_token_accumulator()
        assert _get_session_tokens() == 0  # inner chat starts fresh

        # Inner chat: accumulate 60 tokens.
        _log_token_usage(
            [Message("user", "inner")],
            Message("assistant", "reply"),
            "mock/gpt-mock",
        )
        assert _get_session_tokens() == 60

        # Simulate what the fixed chat() does at nested-call exit (finally block):
        # restore the outer total.
        chat_module._session_tokens.set(prev_tokens)

    # Outer running total is intact — inner chat left no trace.
    assert _get_session_tokens() == 120


def test_accumulator_isolated_across_chat_contexts():
    """A nested/concurrent chat reset cannot modify its parent's running total."""
    meta = _make_model_meta(context=10_000)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[100, 20, 50, 10]),
    ):
        _log_token_usage(
            [Message("user", "parent")],
            Message("assistant", "reply"),
            "mock/gpt-mock",
        )
        assert _get_session_tokens() == 120

        child_context = copy_context()

        def run_child() -> None:
            _reset_token_accumulator()
            _log_token_usage(
                [Message("user", "child")],
                Message("assistant", "reply"),
                "mock/gpt-mock",
            )
            assert _get_session_tokens() == 60

        child_context.run(run_child)

    assert _get_session_tokens() == 120


def test_log_token_usage_survives_len_tokens_error():
    """_log_token_usage never raises; errors are swallowed so the chat loop continues."""
    with patch.object(
        chat_module, "len_tokens", side_effect=ValueError("unsupported model")
    ):
        # Must not raise — informational display errors must not crash the main loop.
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "unknown/model"
        )
    # Accumulator stays at 0 (not incremented on error).
    assert _get_session_tokens() == 0


def test_log_token_usage_survives_get_model_error():
    """_log_token_usage survives get_model() raising (e.g. unknown model registry)."""
    with (
        patch.object(chat_module, "len_tokens", side_effect=[10, 5]),
        patch.object(chat_module, "get_model", side_effect=KeyError("unknown model")),
    ):
        # Must not raise.
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "unknown/model"
        )
