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


def test_anthropic_clients_have_sdk_retries_disabled():
    """The Anthropic clients are constructed with SDK retries disabled."""
    import inspect

    from gptme.llm import llm_anthropic

    source = inspect.getsource(llm_anthropic)
    assert "max_retries=SDK_MAX_RETRIES" in source
    assert "max_retries=5" not in source
