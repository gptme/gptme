"""Tests for subscription-aware cost semantics (pricing_type field)."""

import pytest

from gptme.llm.models import get_model
from gptme.llm.models.listing import model_to_dict
from gptme.telemetry import _calculate_llm_cost

# --- pricing_type resolution ---


@pytest.mark.parametrize(
    "model_str",
    [
        "openai-subscription/gpt-5.6-sol",
        "openai-subscription/gpt-5",
        "openai-subscription/gpt-5.6",  # alias
    ],
)
def test_openai_subscription_pricing_type(model_str):
    """openai-subscription models resolve with pricing_type == 'subscription'."""
    meta = get_model(model_str)
    assert meta.pricing_type == "subscription", (
        f"{model_str!r} expected pricing_type='subscription', got {meta.pricing_type!r}"
    )


def test_grok_subscription_pricing_type():
    """grok-subscription models resolve with pricing_type == 'subscription'."""
    meta = get_model("grok-subscription/grok-4.5")
    assert meta.pricing_type == "subscription"


@pytest.mark.parametrize(
    "model_str",
    [
        "openai/gpt-5",
        "openai/gpt-5.6-sol",
        "anthropic/claude-sonnet-4-6",
        "xai/grok-4",
    ],
)
def test_api_providers_per_token(model_str):
    """Standard API-backed models default to pricing_type == 'per_token'."""
    meta = get_model(model_str)
    assert meta.pricing_type == "per_token"


def test_subscription_models_retain_comparison_prices():
    """Subscription models keep API-equivalent price_input/price_output for comparison."""
    meta = get_model("openai-subscription/gpt-5.6-sol")
    assert meta.price_input > 0 or meta.price_output > 0, (
        "Expected non-zero API-equivalent prices for comparison, got zeros"
    )


# --- telemetry cost calculation ---


def test_subscription_cost_is_zero():
    """_calculate_llm_cost returns 0.0 for subscription models with nonzero tokens."""
    cost = _calculate_llm_cost(
        provider="openai-subscription",
        model="gpt-5.6-sol",
        input_tokens=10_000,
        output_tokens=5_000,
    )
    assert cost == 0.0


def test_grok_subscription_cost_is_zero():
    """_calculate_llm_cost returns 0.0 for grok-subscription models."""
    cost = _calculate_llm_cost(
        provider="grok-subscription",
        model="grok-4.5",
        input_tokens=10_000,
        output_tokens=5_000,
    )
    assert cost == 0.0


def test_per_token_cost_nonzero():
    """_calculate_llm_cost returns a positive value for standard API models."""
    cost = _calculate_llm_cost(
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=10_000,
        output_tokens=5_000,
    )
    assert cost > 0.0


# --- model serialization ---


def test_subscription_model_dict_includes_pricing_type():
    """model_to_dict includes pricing_type for subscription-backed models."""
    meta = get_model("openai-subscription/gpt-5.6-sol")
    d = model_to_dict(meta)
    assert d.get("pricing_type") == "subscription"


def test_per_token_model_dict_omits_pricing_type():
    """model_to_dict omits pricing_type for standard per-token models (default)."""
    meta = get_model("openai/gpt-5")
    d = model_to_dict(meta)
    assert "pricing_type" not in d


def test_subscription_model_dict_includes_comparison_prices():
    """model_to_dict includes price_input/price_output even for subscription models."""
    meta = get_model("grok-subscription/grok-4.5")
    d = model_to_dict(meta)
    assert "price_input" in d or "price_output" in d
    assert d.get("pricing_type") == "subscription"
