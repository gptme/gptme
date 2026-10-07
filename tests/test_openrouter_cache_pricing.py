"""OpenRouter catalog cache rates must reach per-request cost accounting."""

from dataclasses import replace

import pytest

from gptme.llm.llm_openai import openrouter_model_to_modelmeta
from gptme.telemetry import _calculate_llm_cost


def catalog_model(cache_price="0.00000003"):
    pricing = {"prompt": "0.00000015", "completion": "0.00000075"}
    if cache_price is not None:
        pricing["input_cache_read"] = cache_price
    return openrouter_model_to_modelmeta(
        {"id": "z-ai/glm-5.3-flash", "pricing": pricing}
    )


def test_catalog_preserves_cache_read_price():
    assert catalog_model().price_cache_read == pytest.approx(0.03)


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(1000, 100), (0, 0)])
def test_explicit_cache_rate_charged_with_disjoint_tokens(
    monkeypatch, input_tokens, output_tokens
):
    meta = catalog_model()
    seen = []

    def get_model(name):
        seen.append(name)
        return meta

    monkeypatch.setattr("gptme.llm.models.get_model", get_model)
    cost = _calculate_llm_cost(
        "openrouter",
        "z-ai/glm-5.3-flash",
        input_tokens,
        output_tokens,
        cache_read_tokens=1_000_000,
    )
    assert cost == pytest.approx(
        input_tokens * 0.15 / 1e6 + output_tokens * 0.75 / 1e6 + 0.03
    )
    assert seen == ["openrouter/z-ai/glm-5.3-flash"]


@pytest.mark.parametrize(("cache_price", "expected"), [("0", 0.0), (None, None)])
def test_zero_and_missing_cache_price_are_distinct(cache_price, expected):
    assert catalog_model(cache_price).price_cache_read == expected


def test_subscription_remains_zero(monkeypatch):
    meta = replace(catalog_model(), pricing_type="subscription")
    monkeypatch.setattr("gptme.llm.models.get_model", lambda name: meta)
    assert (
        _calculate_llm_cost(
            "openrouter", meta.full, 100, 100, cache_read_tokens=1000000
        )
        == 0
    )


def test_explicit_zero_overrides_provider_multiplier(monkeypatch):
    meta = catalog_model("0")
    monkeypatch.setattr("gptme.llm.models.get_model", lambda name: meta)
    assert _calculate_llm_cost(
        "openai", "test", 1000000, 1, cache_read_tokens=1000000
    ) == pytest.approx(0.15000075)


@pytest.mark.parametrize("cache_price", ["0", "0.00000003", None])
def test_cache_price_serialization(cache_price):
    from gptme.llm.models.listing import model_to_dict

    meta = catalog_model(cache_price)
    serialized = model_to_dict(meta)
    if cache_price is None:
        assert "price_cache_read" not in serialized
    else:
        assert serialized["price_cache_read"] == meta.price_cache_read


@pytest.mark.parametrize(
    ("provider", "model", "rate"),
    [
        # production convention: `_record_usage` passes the underlying vendor as
        # provider and keeps the fully qualified model name
        ("deepseek", "openrouter/deepseek/deepseek-v4.1-flash", 0.006),
        ("z-ai", "openrouter/z-ai/glm-5.3-flash", 0.03),
    ],
)
def test_static_openrouter_models_charge_cache_reads(provider, model, rate):
    assert _calculate_llm_cost(
        provider,
        model,
        0,
        0,
        cache_read_tokens=1000000,
    ) == pytest.approx(rate)


def test_production_openrouter_lookup_uses_full_model(monkeypatch):
    """A vendor provider must not break the qualified OpenRouter model lookup."""
    meta = catalog_model()
    seen = []

    def get_model(name):
        seen.append(name)
        return meta

    monkeypatch.setattr("gptme.llm.models.get_model", get_model)
    cost = _calculate_llm_cost(
        "z-ai",
        "openrouter/z-ai/glm-5.3-flash",
        0,
        0,
        cache_read_tokens=1_000_000,
    )
    assert seen == ["openrouter/z-ai/glm-5.3-flash"]
    assert cost == pytest.approx(0.03)
