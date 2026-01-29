from unittest.mock import patch

from gptme.llm.models import (
    ModelMeta,
    _get_models_for_provider,
    get_model,
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
