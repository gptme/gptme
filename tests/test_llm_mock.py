"""Tests for the built-in offline mock provider."""

import pytest

from gptme.llm import _chat_complete, _stream, init_llm
from gptme.llm.llm_mock import STATIC_RESPONSE, chat, stream
from gptme.llm.models import get_model
from gptme.llm.models.data import MODELS
from gptme.llm.models.types import PROVIDERS
from gptme.message import Message


def test_mock_provider_registered():
    """The mock provider is a built-in provider with scripted models."""
    assert "mock" in PROVIDERS
    assert set(MODELS["mock"]) == {"echo", "static"}


def test_mock_models_resolve():
    """Mock models resolve to ModelMeta with zero cost and no auth."""
    for name in ("echo", "static"):
        meta = get_model(f"mock/{name}")
        assert meta.provider == "mock"
        assert meta.model == name
        assert meta.price_input == 0
        assert meta.price_output == 0


def test_init_llm_mock_requires_no_auth():
    """init_llm('mock') is a no-op and never raises (no client/key needed)."""
    init_llm("mock")  # should not raise even with no API keys configured


def test_echo_chat():
    """mock/echo echoes the last user message."""
    messages = [
        Message("system", "ignored"),
        Message("user", "hello world"),
    ]
    content, meta = chat(messages, "mock/echo", None)
    assert content == "Echo: hello world"
    assert meta == {"model": "mock/echo"}


def test_echo_uses_last_user_message():
    """mock/echo picks the most recent user message, not the first."""
    messages = [
        Message("user", "first"),
        Message("assistant", "reply"),
        Message("user", "second"),
    ]
    content, _ = chat(messages, "mock/echo", None)
    assert content == "Echo: second"


def test_static_chat():
    """mock/static returns a fixed canned response regardless of input."""
    content, _ = chat([Message("user", "anything")], "mock/static", None)
    assert content == STATIC_RESPONSE


def test_unknown_mock_model_raises():
    """An unknown mock model name is an explicit error."""
    with pytest.raises(ValueError, match="Unknown mock model"):
        chat([Message("user", "x")], "mock/does-not-exist", None)


def test_stream_reconstructs_chat_response():
    """Concatenated stream chunks reconstruct the chat() response exactly."""
    messages = [Message("user", "stream me please")]
    chunks = []
    gen = stream(messages, "mock/echo", None)
    try:
        while True:
            chunks.append(next(gen))
    except StopIteration as e:
        meta = e.value
    expected, _ = chat(messages, "mock/echo", None)
    assert "".join(chunks) == expected
    assert meta == {"model": "mock/echo"}
    # Streaming should produce more than one chunk for a multi-word response.
    assert len(chunks) > 1


def test_chat_complete_routes_to_mock():
    """The top-level _chat_complete dispatcher routes mock/* to the mock path."""
    content, _ = _chat_complete([Message("user", "routed")], "mock/echo", None)
    assert content == "Echo: routed"


def test_stream_dispatcher_routes_to_mock():
    """The top-level _stream dispatcher routes mock/* to the mock path."""
    stream_wrapper = _stream([Message("user", "routed")], "mock/echo", None)
    assert "".join(stream_wrapper) == "Echo: routed"
