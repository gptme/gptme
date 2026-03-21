from unittest.mock import patch

import pytest

from gptme.llm.models import (
    MODELS,
    ModelMeta,
    _find_closest_model_properties,
    _get_models_for_provider,
    get_model,
    get_recommended_model,
    list_models,
)


def test_get_static_model():
    """Test getting a model that exists in static MODELS dict."""
    model = get_model("openai/gpt-4o")
    assert model.provider == "openai"
    assert model.model == "gpt-4o"
    assert model.context > 0


def test_get_model_provider_only():
    """Test getting recommended model when only provider is given."""
    model = get_model("openai")
    assert model.provider == "openai"
    assert model.model == "gpt-5"  # current recommended model


def test_get_model_unknown_provider_model():
    """Test fallback for unknown provider/model combination."""
    model = get_model("unknown-provider/unknown-model")
    assert model.provider == "unknown"
    assert model.model == "unknown-provider/unknown-model"
    assert model.context == 128_000  # fallback context


def test_get_model_by_name_only():
    """Test getting model by name only (searches all providers)."""
    model = get_model("gpt-4o")
    assert model.provider == "openai"
    assert model.model == "gpt-4o"


def test_get_model_unknown_name_only():
    """Test fallback for unknown model name without provider."""
    model = get_model("completely-unknown-model")
    assert model.provider == "unknown"
    assert model.model == "completely-unknown-model"
    assert model.context == 128_000


@patch("gptme.llm.models._get_models_for_provider")
def test_get_model_dynamic_fetch_success(mock_get_models):
    """Test successful dynamic model fetching for OpenRouter."""
    # Mock a dynamic model
    dynamic_model = ModelMeta(
        provider="openrouter",
        model="test-dynamic-model",
        context=100_000,
        price_input=1.0,
        price_output=2.0,
    )
    mock_get_models.return_value = [dynamic_model]

    model = get_model("openrouter/test-dynamic-model")
    assert model.provider == "openrouter"
    assert model.model == "test-dynamic-model"
    assert model.context == 100_000
    assert model.price_input == 1.0

    mock_get_models.assert_called_once_with("openrouter", dynamic_fetch=True)


@patch("gptme.llm.models._get_models_for_provider")
def test_get_model_dynamic_fetch_failure(mock_get_models):
    """Test fallback when dynamic model fetching fails."""
    mock_get_models.side_effect = Exception("API error")

    model = get_model("openrouter/test-dynamic-model")
    assert model.provider == "openrouter"
    assert model.model == "test-dynamic-model"
    assert model.context == 128_000  # fallback


@patch("gptme.llm.models._get_models_for_provider")
def test_get_model_dynamic_fetch_model_not_found(mock_get_models):
    """Test fallback when dynamic model is not found in results."""
    other_model = ModelMeta(provider="openrouter", model="other-model", context=100_000)
    mock_get_models.return_value = [other_model]

    model = get_model("openrouter/test-dynamic-model")
    assert model.provider == "openrouter"
    assert model.model == "test-dynamic-model"
    assert model.context == 128_000  # fallback


def test_get_models_for_provider():
    """Test getting models for a specific provider."""
    # Test with static models only
    openai_models = _get_models_for_provider("openai", dynamic_fetch=False)
    assert len(openai_models) > 0
    assert all(m.provider == "openai" for m in openai_models)


@patch("gptme.llm.models._get_models_for_provider")
def test_get_model_name_only_with_dynamic_fetch(mock_get_models):
    """Test model lookup by name only with dynamic fetching from OpenRouter."""
    # Mock OpenRouter dynamic model
    dynamic_model = ModelMeta(
        provider="openrouter", model="test-model", context=100_000
    )
    mock_get_models.return_value = [dynamic_model]

    model = get_model("test-model")
    assert model.provider == "openrouter"
    assert model.model == "test-model"
    assert model.context == 100_000

    # Should have tried OpenRouter dynamic fetch
    mock_get_models.assert_called_with("openrouter", dynamic_fetch=True)


def test_get_model_openrouter_with_subprovider_suffix():
    """Test getting an OpenRouter model with subprovider suffix (e.g., @moonshotai).

    This tests the fix for issue #1180 where models with subprovider suffixes
    like 'openrouter/moonshotai/kimi-k2@moonshotai' were not found in static MODELS.
    """
    # Test without suffix (should work)
    model_no_suffix = get_model("openrouter/moonshotai/kimi-k2")
    assert model_no_suffix.provider == "openrouter"
    assert model_no_suffix.model == "moonshotai/kimi-k2"
    assert model_no_suffix.context == 262_144

    # Test with suffix (this was the bug - would return fallback 128k context)
    model_with_suffix = get_model("openrouter/moonshotai/kimi-k2@moonshotai")
    assert model_with_suffix.provider == "openrouter"
    assert (
        model_with_suffix.model == "moonshotai/kimi-k2@moonshotai"
    )  # preserves original name
    assert model_with_suffix.context == 262_144  # should match the non-suffix version

    # Verify price is also correct (not fallback $0)
    assert model_with_suffix.price_input == model_no_suffix.price_input
    assert model_with_suffix.price_output == model_no_suffix.price_output


def test_get_model_openrouter_subprovider_suffix_not_in_static():
    """Test that models with subprovider suffix not in static MODELS still work via dynamic fetch."""
    # This model doesn't exist in static MODELS, so it should try dynamic fetch
    # We can't easily mock here, but we can verify it doesn't crash and returns something
    model = get_model("openrouter/anthropic/claude-3-opus@anthropic")
    # Should either find it via dynamic fetch or return fallback
    assert model.provider == "openrouter"
    # The model name should preserve the suffix
    assert "@anthropic" in model.model


@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("openai", "gpt-5"),
        ("anthropic", "claude-sonnet-4-6"),
        ("gemini", "gemini-2.5-pro"),
        ("openrouter", "meta-llama/llama-3.1-405b-instruct"),
        ("xai", "grok-4"),
        ("deepseek", "deepseek-chat"),
        ("groq", "llama-3.3-70b-versatile"),
        ("openai-subscription", "gpt-5.4"),
    ],
)
def test_get_recommended_model(provider, expected_model):
    """Test that all providers with models have a recommended default."""
    result = get_recommended_model(provider)
    assert result == expected_model
    # Verify the recommended model actually exists in MODELS
    if MODELS.get(provider):
        assert result in MODELS[provider], (
            f"Recommended model '{result}' not found in MODELS['{provider}']"
        )


@pytest.mark.parametrize("provider", ["azure", "nvidia", "local"])
def test_get_recommended_model_raises_for_unconfigured(provider):
    """Test that providers without default models raise with a helpful message."""
    with pytest.raises(ValueError, match="requires specifying a model"):
        get_recommended_model(provider)


def test_get_model_provider_only_deepseek():
    """Test that 'gptme -m deepseek' resolves to deepseek-chat."""
    model = get_model("deepseek")
    assert model.provider == "deepseek"
    assert model.model == "deepseek-chat"


def test_get_model_provider_only_groq():
    """Test that 'gptme -m groq' resolves to llama-3.3-70b-versatile."""
    model = get_model("groq")
    assert model.provider == "groq"
    assert model.model == "llama-3.3-70b-versatile"


@patch("gptme.llm.models._get_configured_providers")
def test_list_models_available_only(mock_configured, capsys):
    """Test that --available filters to only configured providers."""
    mock_configured.return_value = {"anthropic"}

    list_models(available_only=True, dynamic_fetch=False)
    output = capsys.readouterr().out

    assert "anthropic" in output
    # Should not contain unconfigured providers
    assert "\nopenai" not in output
    assert "\ngemini" not in output


@patch("gptme.llm.models._get_configured_providers")
def test_list_models_shows_availability_markers(mock_configured, capsys):
    """Test that detailed format shows availability markers."""
    mock_configured.return_value = {"anthropic", "openai"}

    list_models(provider_filter="anthropic", dynamic_fetch=False)
    output = capsys.readouterr().out

    assert "[✓]" in output


@patch("gptme.llm.models._get_configured_providers")
def test_list_models_unconfigured_marker(mock_configured, capsys):
    """Test that unconfigured providers show ✗ marker."""
    mock_configured.return_value = set()

    list_models(provider_filter="anthropic", dynamic_fetch=False)
    output = capsys.readouterr().out

    assert "[✗]" in output


@patch("gptme.llm.models._get_configured_providers")
def test_list_models_simple_available(mock_configured, capsys):
    """Test that --simple --available filters correctly."""
    mock_configured.return_value = {"anthropic"}

    list_models(simple_format=True, available_only=True, dynamic_fetch=False)
    output = capsys.readouterr().out

    lines = [line for line in output.strip().split("\n") if line]
    # All lines should be anthropic models
    assert all("anthropic/" in line for line in lines)
    # Should not contain any other provider
    assert not any("openai/" in line for line in lines)


# --- Tests for closest-match heuristic ---


class TestClosestModelMatch:
    """Tests for _find_closest_model_properties and its integration in get_model."""

    def test_unknown_anthropic_sonnet_uses_closest_sonnet(self):
        """An unknown claude-sonnet variant should inherit from the latest known sonnet."""
        model = get_model("anthropic/claude-sonnet-5-0")
        assert model.provider == "anthropic"
        assert model.model == "claude-sonnet-5-0"
        # Should get real metadata from closest sonnet, not generic fallback
        assert model.context == 1_000_000  # claude-sonnet-4-6 has 1M context (GA)
        assert model.supports_vision is True
        assert model.price_input > 0
        assert model.price_output > 0

    def test_unknown_anthropic_opus_uses_closest_opus(self):
        """An unknown claude-opus variant should inherit from the latest known opus."""
        model = get_model("anthropic/claude-opus-5-0")
        assert model.provider == "anthropic"
        assert model.context == 1_000_000  # claude-opus-4-6 has 1M context (GA)
        assert model.supports_reasoning is True
        # Opus is more expensive than sonnet
        assert model.price_input >= 5

    def test_unknown_openai_gpt_uses_closest_gpt(self):
        """An unknown GPT model should inherit from the latest known GPT."""
        model = get_model("openai/gpt-6")
        assert model.provider == "openai"
        assert model.context > 0
        assert model.price_input > 0

    def test_unknown_gemini_uses_closest_gemini(self):
        """An unknown Gemini model should inherit from the closest known Gemini."""
        model = get_model("gemini/gemini-4.0-pro")
        assert model.provider == "gemini"
        assert model.context >= 1_000_000  # Gemini models have large contexts
        assert model.price_input > 0

    def test_closest_match_skips_deprecated_models(self):
        """Closest match should prefer non-deprecated models."""
        props = _find_closest_model_properties("anthropic", "claude-sonnet-99")
        assert props is not None
        assert not props.get("deprecated", False)

    def test_closest_match_falls_back_to_recommended(self):
        """When no family prefix matches, fall back to recommended model."""
        # "totally-new-model" doesn't match any family prefix in anthropic
        props = _find_closest_model_properties("anthropic", "totally-new-model")
        assert props is not None
        # Should get recommended model (claude-sonnet-4-6) properties
        assert props["context"] == 1_000_000  # claude-sonnet-4-6 has 1M context (GA)

    def test_closest_match_empty_provider_returns_none(self):
        """Providers with no models in registry return None."""
        props = _find_closest_model_properties("azure", "some-model")
        assert props is None

    def test_closest_match_unknown_provider_returns_none(self):
        """Providers not in MODELS at all return None."""
        props = _find_closest_model_properties("nonexistent", "some-model")  # type: ignore[arg-type]
        assert props is None

    def test_get_model_unknown_anthropic_not_generic_fallback(self):
        """Verify unknown Anthropic models don't get $0 pricing (the old behavior)."""
        model = get_model("anthropic/claude-haiku-5-0")
        # Old behavior: price_input=0, price_output=0
        # New behavior: inherits from closest haiku match
        assert model.price_input > 0
        assert model.price_output > 0

    def test_get_model_unknown_xai_uses_closest_grok(self):
        """An unknown grok model should match the grok family."""
        model = get_model("xai/grok-5")
        assert model.provider == "xai"
        assert model.supports_reasoning is True
        assert model.price_input > 0


class TestSupportsParallelToolCalls:
    """Test the supports_parallel_tool_calls flag on ModelMeta."""

    def test_claude_sonnet_4_6_supports_parallel(self):
        """claude-sonnet-4-6 was explicitly verified to support parallel tool calls."""
        model = get_model("anthropic/claude-sonnet-4-6")
        assert model.supports_parallel_tool_calls is True

    def test_claude_opus_4_6_supports_parallel(self):
        model = get_model("anthropic/claude-opus-4-6")
        assert model.supports_parallel_tool_calls is True

    def test_claude_haiku_4_5_does_not_support_parallel(self):
        """claude-haiku-4-5 was explicitly verified to NOT emit parallel tool calls."""
        model = get_model("anthropic/claude-haiku-4-5-20251001")
        assert model.supports_parallel_tool_calls is False

    def test_claude_3_5_sonnet_does_not_support_parallel(self):
        """Older claude-3.x models do not support parallel tool calls."""
        model = get_model("anthropic/claude-3-5-sonnet-20241022")
        assert model.supports_parallel_tool_calls is False

    def test_gpt5_supports_parallel(self):
        model = get_model("openai/gpt-5")
        assert model.supports_parallel_tool_calls is True

    def test_gpt4o_supports_parallel(self):
        model = get_model("openai/gpt-4o")
        assert model.supports_parallel_tool_calls is True

    def test_openrouter_claude_sonnet_4_6_supports_parallel(self):
        """claude-sonnet-4-6 via OpenRouter should also support parallel tool calls."""
        model = get_model("openrouter/anthropic/claude-sonnet-4-6")
        assert model.supports_parallel_tool_calls is True

    def test_unknown_model_defaults_to_false(self):
        """Unknown models fall back to False (safe default — don't break things)."""
        model = get_model("unknown-provider/unknown-model")
        assert model.supports_parallel_tool_calls is False
