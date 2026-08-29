"""Tests for the shared LLM retry policy.

Regression tests for https://github.com/gptme/gptme/issues/3668:
provider SDKs retried on their own on top of gptme's retry loop (so one 429
became sdk_retries * max_retries requests), and the backoff window was too
short to outlast a brief upstream rate-limit.
"""

import pytest

from gptme.llm.retry_policy import (
    DEFAULT_BASE_DELAY,
    DEFAULT_MAX_RETRIES,
    MAX_RETRY_DELAY,
    SDK_MAX_RETRIES,
    get_max_retries,
    retry_delay,
)


def test_backoff_is_exponential_then_capped():
    assert retry_delay(0) == DEFAULT_BASE_DELAY
    assert retry_delay(1) == 2 * DEFAULT_BASE_DELAY
    assert retry_delay(2) == 4 * DEFAULT_BASE_DELAY
    # Without a cap, attempt 10 would sleep for over 17 minutes
    assert retry_delay(10) == MAX_RETRY_DELAY


def test_default_retry_window_is_about_five_minutes():
    """A <1min upstream blip must not kill a long autonomous session."""
    total = sum(retry_delay(a) for a in range(DEFAULT_MAX_RETRIES - 1))
    assert 280 <= total <= 360, f"retry window is {total}s, expected ~5min"


def test_sdk_retries_are_disabled():
    """gptme owns retries; SDK-level retries would multiply the attempts."""
    assert SDK_MAX_RETRIES == 0


def test_max_retries_env_override(monkeypatch):
    monkeypatch.setenv("GPTME_LLM_MAX_RETRIES", "3")
    assert get_max_retries() == 3


@pytest.mark.parametrize("value", ["not-a-number", "0", "-1"])
def test_invalid_max_retries_falls_back_to_default(monkeypatch, value):
    monkeypatch.setenv("GPTME_LLM_MAX_RETRIES", value)
    assert get_max_retries() == DEFAULT_MAX_RETRIES


def test_openai_lazy_client_materializes_with_sdk_retries_disabled(monkeypatch):
    """The production lazy-client path must disable retries before construction."""
    from gptme.config import Config
    from gptme.llm import llm_openai

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delitem(llm_openai.clients, "openai", raising=False)
    llm_openai.init("openai", Config())

    client = llm_openai.get_client("openai")
    assert isinstance(client, llm_openai._LazyClient)
    assert client._kwargs["max_retries"] == SDK_MAX_RETRIES
    assert client._materialize().max_retries == SDK_MAX_RETRIES


@pytest.mark.parametrize(
    ("provider", "env_var", "extra_env"),
    [
        ("openai", "OPENAI_API_KEY", {}),
        ("openrouter", "OPENROUTER_API_KEY", {}),
        ("groq", "GROQ_API_KEY", {}),
        ("deepseek", "DEEPSEEK_API_KEY", {}),
        ("xai", "XAI_API_KEY", {}),
        ("gemini", "GEMINI_API_KEY", {}),
        ("moonshot", "MOONSHOT_API_KEY", {}),
        ("nvidia", "NVIDIA_API_KEY", {}),
        (
            "azure",
            "AZURE_OPENAI_API_KEY",
            {"AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com"},
        ),
        ("local", "OPENAI_API_KEY", {"OPENAI_BASE_URL": "http://localhost:11434/v1"}),
    ],
)
def test_openai_compat_providers_disable_sdk_retries(
    monkeypatch, provider, env_var, extra_env
):
    """Every OpenAI-compatible init path must disable SDK retries, not just openai."""
    from gptme.config import Config
    from gptme.llm import llm_openai

    monkeypatch.setenv(env_var, "test-key")
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delitem(llm_openai.clients, provider, raising=False)
    llm_openai.init(provider, Config())

    client = llm_openai.get_client(provider)
    assert isinstance(client, llm_openai._LazyClient)
    assert client._kwargs["max_retries"] == SDK_MAX_RETRIES


def test_is_provider_error_requires_reply_origin_tag():
    """Untagged SDK/httpx errors (tools, hooks) do not recover; tagged ones do."""
    from unittest.mock import MagicMock

    import httpx
    from openai import RateLimitError

    from gptme.llm import is_provider_error, mark_llm_reply_origin

    response = MagicMock()
    response.status_code = 429
    sdk_err = RateLimitError("upstream", response=response, body=None)
    assert not is_provider_error(sdk_err)
    mark_llm_reply_origin(sdk_err)
    assert is_provider_error(sdk_err)

    tool_err = httpx.ConnectError("browser failed")
    assert not is_provider_error(tool_err)
    mark_llm_reply_origin(tool_err)
    assert is_provider_error(tool_err)

    bug = ValueError("bug in gptme")
    mark_llm_reply_origin(bug)
    assert not is_provider_error(bug)

    # openai-subscription / grok-subscription talk HTTP via `requests`, not the
    # OpenAI SDK. Tagged requests errors must recover; untagged ones must not
    # (a tool using `requests` is still a tool bug).
    import requests

    tagged_http = requests.HTTPError("Codex API error 429: usage_limit_reached")
    assert not is_provider_error(tagged_http)
    mark_llm_reply_origin(tagged_http)
    assert is_provider_error(tagged_http)

    tagged_timeout = requests.exceptions.Timeout("read timeout")
    mark_llm_reply_origin(tagged_timeout)
    assert is_provider_error(tagged_timeout)


def test_reply_does_not_tag_generation_pre_hook_errors(monkeypatch):
    """httpx from GENERATION_PRE must not look like a recoverable LLM failure."""
    import httpx

    from gptme.llm import is_provider_error, reply
    from gptme.message import Message

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("hook fetch failed")

    monkeypatch.setattr("gptme.hooks.trigger_hook", boom)

    with pytest.raises(httpx.ConnectError, match="hook fetch failed") as ei:
        reply([Message("user", "hi")], "openai/gpt-4")
    assert not is_provider_error(ei.value)


def test_reply_tags_provider_call_errors(monkeypatch):
    """httpx/SDK errors from the provider call after hooks are recoverable."""
    import httpx

    from gptme.llm import is_provider_error, reply
    from gptme.message import Message

    monkeypatch.setattr("gptme.hooks.trigger_hook", lambda *_a, **_k: iter([]))
    monkeypatch.setattr("gptme.llm.init_llm", lambda *_a, **_k: None)

    def fail_complete(*_args, **_kwargs):
        raise httpx.ConnectError("upstream")

    monkeypatch.setattr("gptme.llm._chat_complete", fail_complete)

    with pytest.raises(httpx.ConnectError, match="upstream") as ei:
        reply([Message("user", "hi")], "openai/gpt-4", stream=False)
    assert is_provider_error(ei.value)


def test_anthropic_clients_have_sdk_retries_disabled():
    """The Anthropic clients are constructed with SDK retries disabled."""
    import inspect

    from gptme.llm import llm_anthropic

    source = inspect.getsource(llm_anthropic)
    assert "max_retries=SDK_MAX_RETRIES" in source
    assert "max_retries=5" not in source
