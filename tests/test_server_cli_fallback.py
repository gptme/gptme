"""Tests for server CLI provider-aware fallback model selection."""

from unittest.mock import patch

from gptme.server.constants import (
    DEFAULT_FALLBACK_MODEL,
    PROVIDER_FALLBACK_MODELS,
    _pick_fallback_model,
)


def test_pick_fallback_prefers_available_provider():
    """When OpenAI is available but not Anthropic, fallback should use OpenAI."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[("openai", "OPENAI_API_KEY")],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openai"]


def test_pick_fallback_uses_first_available_provider():
    """Fallback picks the first provider returned by list_available_providers."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[
            ("openrouter", "OPENROUTER_API_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openrouter"]


def test_pick_fallback_returns_default_when_no_providers():
    """Without configured providers, fall back to the default model.

    The init() call that follows will then raise a clear error — the fallback
    is not meant to be useful in this case, just to preserve existing behavior.
    """
    with patch("gptme.llm.list_available_providers", return_value=[]):
        assert _pick_fallback_model() == DEFAULT_FALLBACK_MODEL


def test_pick_fallback_skips_unknown_providers():
    """If list_available_providers returns a provider we don't have a fallback
    model for, skip it and try the next one."""
    with patch(
        "gptme.llm.list_available_providers",
        return_value=[
            ("some-unknown-plugin", "SOME_KEY"),
            ("openai", "OPENAI_API_KEY"),
        ],
    ):
        assert _pick_fallback_model() == PROVIDER_FALLBACK_MODELS["openai"]


def test_provider_fallback_models_includes_all_api_key_providers():
    """PROVIDER_FALLBACK_MODELS should cover every API-key provider gptme
    supports (except azure, which requires a tenant-specific deployment name).
    """
    from gptme.llm import PROVIDER_API_KEYS

    expected = set(PROVIDER_API_KEYS.keys()) - {"azure"}
    missing = expected - set(PROVIDER_FALLBACK_MODELS.keys())
    assert not missing, f"Missing fallback models for providers: {missing}"
