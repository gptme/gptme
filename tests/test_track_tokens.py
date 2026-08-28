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
    # Occupancy is input+output (1000+50) against the 10k window.
    assert "context: 1,050 / 10,000 (10.5%)" in captured.err
    assert "+50 out" in captured.err
    assert "session total: 1,050" in captured.err


def test_output_percentage_includes_output_tokens(capsys):
    """Percentage uses n_in+n_out so a large completion cannot hide overflow."""
    meta = _make_model_meta(context=10_000)

    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[9_000, 2_000]),
    ):
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "mock/gpt-mock"
        )

    captured = capsys.readouterr()
    # n_in-only would show 90.0%; occupancy after the call is 110.0%.
    assert "context: 11,000 / 10,000 (110.0%)" in captured.err
    assert "+2,000 out" in captured.err
    assert "90.0%" not in captured.err


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
    assert "context: 120 / unknown" in captured.err
    assert "+20 out" in captured.err
    assert "session total: 120" in captured.err


def test_accumulator_restored_after_chat_returns(tmp_path):
    """chat() restores its caller's token accumulator in its finally block."""
    meta = _make_model_meta()
    with (
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[100, 20, 50, 10]),
    ):
        _log_token_usage(
            [Message("user", "outer")],
            Message("assistant", "reply"),
            "mock/gpt-mock",
        )
    assert _get_session_tokens() == 120

    with (
        patch.object(chat_module, "set_current_conv_name"),
        patch.object(chat_module, "set_conversation_context"),
        patch.object(chat_module, "init"),
        patch.object(chat_module, "trigger_hook", return_value=[]),
        patch.object(chat_module, "get_default_model", return_value=None),
        patch.object(chat_module, "get_model", return_value=meta),
        patch.object(chat_module, "len_tokens", side_effect=[50, 10]),
        patch.object(chat_module, "_run_chat_loop") as run_chat_loop,
    ):
        run_chat_loop.side_effect = lambda *args, **kwargs: _log_token_usage(
            [Message("user", "inner")],
            Message("assistant", "reply"),
            "mock/gpt-mock",
        )
        chat_module.chat(
            prompt_msgs=[],
            initial_msgs=[],
            logdir=tmp_path,
            workspace=tmp_path,
            model="mock/gpt-mock",
            tool_format="markdown",
        )

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
        # Must not raise or commit a partial count.
        _log_token_usage(
            [Message("user", "hi")], Message("assistant", "ok"), "unknown/model"
        )
    assert _get_session_tokens() == 0
