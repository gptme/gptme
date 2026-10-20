"""OpenRouter's billed ``usage.cost`` must win over catalog cost estimates."""

import pytest
from openai.types.completion_usage import CompletionUsage

from gptme.llm.llm_openai import _record_usage
from gptme.util.cost_tracker import CostTracker

ESTIMATE = 0.5


@pytest.fixture(autouse=True)
def catalog_estimate(monkeypatch):
    monkeypatch.setattr(
        "gptme.telemetry._calculate_llm_cost", lambda **kwargs: ESTIMATE
    )
    monkeypatch.setattr(
        "gptme.llm.llm_openai._calculate_llm_cost", lambda **kwargs: ESTIMATE
    )
    CostTracker.reset()
    CostTracker.start_session("test-openrouter-reported-cost")
    yield
    CostTracker.reset()


def _usage(**extra):
    return CompletionUsage.model_validate(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, **extra}
    )


def _tracked_cost() -> float:
    costs = CostTracker.get_session_costs()
    assert costs is not None
    return sum(entry.cost for entry in costs.entries)


@pytest.mark.parametrize("reported", [0.00123, 0, 0.0])
def test_openrouter_reported_cost_is_recorded(reported):
    metadata = _record_usage(_usage(cost=reported), "openrouter/z-ai/glm-5.3-flash")

    assert metadata is not None
    assert metadata["cost"] == pytest.approx(reported)
    assert metadata["cost_source"] == "provider"
    assert _tracked_cost() == pytest.approx(reported)


@pytest.mark.parametrize(
    "usage",
    [
        _usage(),
        _usage(cost=None),
        _usage(cost=-1),
        _usage(cost="0.1"),
        _usage(cost=0.00001, is_byok=True),
    ],
    ids=["missing", "null", "negative", "string", "byok-fee-only"],
)
def test_invalid_or_missing_reported_cost_falls_back_to_estimate(usage):
    metadata = _record_usage(usage, "openrouter/z-ai/glm-5.3-flash")

    assert metadata is not None
    assert metadata["cost"] == ESTIMATE
    assert "cost_source" not in metadata
    assert _tracked_cost() == pytest.approx(ESTIMATE)


@pytest.mark.parametrize(
    "model", ["openai/gpt-5", "gptme/openrouter/z-ai/glm-5.3-flash"]
)
def test_cost_field_ignored_outside_direct_openrouter(model):
    metadata = _record_usage(_usage(cost=0.00123), model)

    assert metadata is not None
    assert metadata["cost"] == ESTIMATE
    assert "cost_source" not in metadata
