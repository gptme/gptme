"""An exhausted provider stream with no output must not become an empty assistant turn."""

from collections.abc import Generator

import pytest

from gptme.llm import (
    EmptyStreamError,
    _reply_stream,
    _StreamWithMetadata,
    is_llm_reply_error,
    mark_llm_reply_origin,
)
from gptme.message import Message, MessageMetadata

MODEL = "openai/gpt-5.6-sol"


def _fake_stream(chunks: list[str], metadata: MessageMetadata):
    def gen() -> Generator[str, None, MessageMetadata]:
        yield from chunks
        return metadata

    def _stream(messages, model, tools, **kwargs):
        return _StreamWithMetadata(gen(), model)

    return _stream


def test_zero_chunk_stream_is_rejected(monkeypatch: pytest.MonkeyPatch):
    metadata: MessageMetadata = {
        "model": MODEL,
        "served_model": "gpt-5.6-sol",
        "usage": {"output_tokens": 166},
    }
    monkeypatch.setattr("gptme.llm._stream", _fake_stream([], metadata))

    with pytest.raises(EmptyStreamError) as excinfo:
        _reply_stream([Message("user", "hi")], MODEL, None)

    assert excinfo.value.metadata == metadata
    assert "166" in str(excinfo.value)


def test_whitespace_only_stream_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "gptme.llm._stream", _fake_stream(["\n", " "], {"model": MODEL})
    )

    with pytest.raises(EmptyStreamError):
        _reply_stream([Message("user", "hi")], MODEL, None)


def test_text_without_tools_is_still_returned(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "gptme.llm._stream", _fake_stream(["hello", " world"], {"model": MODEL})
    )

    msg = _reply_stream([Message("user", "hi")], MODEL, None)

    assert msg.role == "assistant"
    assert msg.content == "hello world"


def test_reasoning_only_output_is_not_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "gptme.llm._stream",
        _fake_stream(["<think>\n", "pondering\n", "</think>\n"], {"model": MODEL}),
    )

    msg = _reply_stream([Message("user", "hi")], MODEL, None)

    assert "pondering" in msg.content


def test_empty_stream_error_takes_provider_recovery_path():
    err = EmptyStreamError(MODEL, None)
    mark_llm_reply_origin(err)

    assert is_llm_reply_error(err)


def test_direct_reply_stream_caller_gets_tagged_error(
    monkeypatch: pytest.MonkeyPatch,
):
    """A direct `_reply_stream` caller bypasses `reply()`'s outer tagging
    handler, so the raise site itself must tag the error."""
    monkeypatch.setattr("gptme.llm._stream", _fake_stream([], {"model": MODEL}))

    with pytest.raises(EmptyStreamError) as excinfo:
        _reply_stream([Message("user", "hi")], MODEL, None)

    assert is_llm_reply_error(excinfo.value)
    output_emitted = getattr(excinfo.value, "_gptme_llm_reply_output_emitted", True)
    assert output_emitted is False
