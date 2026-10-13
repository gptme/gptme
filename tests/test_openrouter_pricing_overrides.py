"""OpenRouter ``pricing.overrides`` long-context tiers must reach cost accounting."""

import pytest

from gptme.llm.llm_openai import openrouter_model_to_modelmeta
from gptme.llm.models.types import PriceTier
from gptme.telemetry import _calculate_llm_cost

# Shape of the live catalog entry for anthropic/claude-haiku-5.5 (2026-10-13):
# 5x input/output and cache-read above 100k prompt tokens.
CATALOG = {
    "id": "anthropic/claude-haiku-5.5",
    "pricing": {
        "prompt": "0.0000001",
        "completion": "0.0000005",
        "input_cache_read": "0.00000001",
        "overrides": [
            {
                "min_prompt_tokens": 100000,
                "prompt": "0.0000005",
                "completion": "0.0000025",
                "input_cache_read": "0.00000005",
            }
        ],
    },
}


@pytest.fixture(autouse=True)
def no_live_catalog(monkeypatch):
    monkeypatch.setattr("gptme.llm.get_available_models", lambda provider: [])


def test_mapper_preserves_override_tiers():
    meta = openrouter_model_to_modelmeta(CATALOG)
    assert meta.price_input == pytest.approx(0.1)
    (tier,) = meta.price_tiers
    assert isinstance(tier, PriceTier)
    assert tier.min_prompt_tokens == 100000
    assert tier.price_input == pytest.approx(0.5)
    assert tier.price_output == pytest.approx(2.5)
    assert tier.price_cache_read == pytest.approx(0.05)


def test_mapper_without_overrides_has_no_tiers():
    meta = openrouter_model_to_modelmeta(
        {"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.0000001"}}
    )
    assert meta.price_tiers == ()


@pytest.mark.parametrize(
    ("prompt_tokens", "expected_input"),
    [(0, 0.1), (99_999, 0.1), (100_000, 0.5), (250_000, 0.5)],
)
def test_effective_prices_select_tier_by_prompt_size(prompt_tokens, expected_input):
    meta = openrouter_model_to_modelmeta(CATALOG)
    price_input, _, _ = meta.effective_prices(prompt_tokens)
    assert price_input == pytest.approx(expected_input)


def test_effective_prices_tier_without_cache_rate_keeps_base():
    meta = openrouter_model_to_modelmeta(
        {
            "id": "x/y",
            "pricing": {
                "prompt": "0.000001",
                "input_cache_read": "0.0000001",
                "overrides": [{"min_prompt_tokens": 10, "prompt": "0.000002"}],
            },
        }
    )
    price_input, price_output, price_cache_read = meta.effective_prices(10)
    assert price_input == pytest.approx(2.0)
    assert price_output == 0  # base completion price (unset -> 0)
    assert price_cache_read == pytest.approx(0.1)


def test_later_tier_without_cache_rate_resets_to_base():
    meta = openrouter_model_to_modelmeta(
        {
            "id": "x/y",
            "pricing": {
                "prompt": "0.000001",
                "input_cache_read": "0.0000001",
                "overrides": [
                    {"min_prompt_tokens": 10, "input_cache_read": "0.0000002"},
                    {"min_prompt_tokens": 20, "prompt": "0.000003"},
                ],
            },
        }
    )
    assert meta.effective_prices(10)[2] == pytest.approx(0.2)
    assert meta.effective_prices(20) == pytest.approx((3.0, 0.0, 0.1))


def test_long_prompt_is_billed_at_tier_rate(monkeypatch):
    meta = openrouter_model_to_modelmeta(CATALOG)
    monkeypatch.setattr("gptme.llm.models.get_model", lambda name: meta)
    # 120k fresh input + 30k cache reads: the prompt is 150k tokens, above the tier.
    cost = _calculate_llm_cost(
        "openrouter",
        "anthropic/claude-haiku-5.5",
        120_000,
        1_000,
        cache_read_tokens=30_000,
    )
    assert cost == pytest.approx(
        120_000 * 0.5 / 1e6 + 1_000 * 2.5 / 1e6 + 30_000 * 0.05 / 1e6
    )


@pytest.mark.parametrize("suffix", ["", "@anthropic"])
@pytest.mark.parametrize("prompt_tokens", [199_999, 200_000])
def test_static_openrouter_cost_uses_catalog_pricing(
    monkeypatch, suffix, prompt_tokens
):
    from gptme.llm.models import get_model

    name = f"openrouter/anthropic/claude-sonnet-4.6{suffix}"
    static = get_model(name)
    assert static.price_tiers == ()
    catalog = openrouter_model_to_modelmeta(
        {
            "id": "anthropic/claude-sonnet-4.6",
            "pricing": {
                "prompt": "0.000003",
                "completion": "0.000015",
                "input_cache_read": "0.0000003",
                "overrides": [
                    {
                        "min_prompt_tokens": 200_000,
                        "prompt": "0.000006",
                        "completion": "0.0000225",
                        "input_cache_read": "0.0000006",
                    }
                ],
            },
        }
    )
    monkeypatch.setattr("gptme.llm.get_available_models", lambda provider: [catalog])
    # Exercise real get_model(), including static metadata and @ routing suffixes.
    cost = _calculate_llm_cost(
        "anthropic",
        name,
        prompt_tokens - 100_000,
        1_000,
        cache_read_tokens=100_000,
    )
    price_in, price_out, price_cache = catalog.effective_prices(prompt_tokens)
    assert price_cache is not None
    assert cost == pytest.approx(
        (prompt_tokens - 100_000) * price_in / 1e6
        + 1_000 * price_out / 1e6
        + 100_000 * price_cache / 1e6
    )
    assert get_model(name) == static  # catalog pricing must not mutate capabilities


@pytest.mark.parametrize("unavailable", [False, True])
def test_static_openrouter_cost_falls_back_without_catalog(monkeypatch, unavailable):
    from gptme.llm.models import get_model

    def fetch(provider):
        if unavailable:
            raise RuntimeError("catalog unavailable")
        return []

    monkeypatch.setattr("gptme.llm.get_available_models", fetch)
    name = "openrouter/anthropic/claude-sonnet-4.6"
    static = get_model(name)
    assert _calculate_llm_cost("openrouter", name, 250_000, 1_000) == pytest.approx(
        250_000 * static.price_input / 1e6 + 1_000 * static.price_output / 1e6
    )


def test_short_prompt_is_billed_at_base_rate(monkeypatch):
    meta = openrouter_model_to_modelmeta(CATALOG)
    monkeypatch.setattr("gptme.llm.models.get_model", lambda name: meta)
    cost = _calculate_llm_cost(
        "openrouter", "anthropic/claude-haiku-5.5", 50_000, 1_000
    )
    assert cost == pytest.approx(50_000 * 0.1 / 1e6 + 1_000 * 0.5 / 1e6)
