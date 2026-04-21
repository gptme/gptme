"""Unit tests for the LiteLLM provider shim."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest import mock

import pytest

from gptme.llm.litellm import LiteLLMClient
from gptme.llm.models import PROVIDERS, PROVIDERS_OPENAI, get_model


def _install_litellm_stub() -> mock.MagicMock:
    """Register a fake ``litellm`` module so ``import litellm`` resolves in tests."""
    fake = types.ModuleType("litellm")
    fake.completion = mock.MagicMock(name="litellm.completion")
    sys.modules["litellm"] = fake
    return fake.completion


def _fake_chat_completion(content: str = "hi") -> SimpleNamespace:
    message = SimpleNamespace(content=content, role="assistant", tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop", index=0)
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8)
    return SimpleNamespace(choices=[choice], usage=usage, model="test")


def test_litellm_client_exposes_openai_surface():
    client = LiteLLMClient()
    assert hasattr(client.chat.completions, "create")
    # `_is_proxy` in llm_openai.py reads these attributes; both should exist.
    assert client.base_url is None
    assert client.api_key is None


def test_litellm_client_forwards_to_litellm_completion():
    completion = _install_litellm_stub()
    completion.return_value = _fake_chat_completion("pong")

    client = LiteLLMClient()
    resp = client.chat.completions.create(
        model="anthropic/claude-3-5-sonnet-20241022",
        messages=[{"role": "user", "content": "ping"}],
    )

    assert resp.choices[0].message.content == "pong"
    completion.assert_called_once()
    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == "anthropic/claude-3-5-sonnet-20241022"
    assert kwargs["messages"] == [{"role": "user", "content": "ping"}]


def test_litellm_registered_as_builtin_provider():
    assert "litellm" in PROVIDERS


def test_litellm_routes_through_openai_path():
    """Providers in PROVIDERS_OPENAI are dispatched via the OpenAI-compatible code
    path in llm_openai.py; litellm relies on that dispatch to reach the shim."""
    assert "litellm" in PROVIDERS_OPENAI


def test_get_model_accepts_litellm_prefix():
    """``get_model('litellm/<prov>/<model>')`` must not raise; it should return a
    ModelMeta so the CLI can accept the model arg even without an entry in the
    static MODELS dict."""
    meta = get_model("litellm/anthropic/claude-3-5-sonnet-20241022")
    assert meta.model == "anthropic/claude-3-5-sonnet-20241022"
    # Generic 128k fallback is fine for LiteLLM routing — provider-specific context
    # limits are enforced by the downstream provider (LiteLLM raises on exceed).
    assert meta.context > 0


def test_get_recommended_model_errors_for_litellm():
    from gptme.llm.models.resolution import get_recommended_model

    with pytest.raises(ValueError, match="LiteLLM requires an explicit model name"):
        get_recommended_model("litellm")
