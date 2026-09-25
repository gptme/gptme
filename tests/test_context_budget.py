"""Tests for the context budget computation (Phase 1 of #3812)."""

import math

import pytest

from gptme.config import (
    Config,
    ContextConfig,
    ProjectConfig,
    UserConfig,
    get_config,
    load_user_config,
    set_config,
)
from gptme.util.context_budget import get_context_budget


def test_default_budget_is_fraction_of_window():
    """Default budget is min(0.9 × window, window − max_output − headroom)."""
    window = 200_000
    budget = get_context_budget(window)
    # Must be below the window
    assert budget < window
    # Must be at least a reasonable fraction
    assert budget >= int(0.85 * window)


def test_default_budget_respects_output_headroom():
    """For normal windows, output+headroom reservation is respected."""
    window = 100_000
    max_output = 8192
    headroom = 1000
    budget = get_context_budget(window, max_output=max_output, headroom=headroom)
    # budget ≤ window − max_output − headroom (90% of 100k = 90k vs 100k-9192=90808)
    assert budget <= window - max_output - headroom


def test_env_budget_absolute(monkeypatch):
    """GPTME_CONTEXT_BUDGET as absolute token count overrides default."""
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "150000")
    budget = get_context_budget(200_000)
    assert budget == 150_000


def test_env_budget_fraction(monkeypatch):
    """GPTME_CONTEXT_BUDGET as fraction of window."""
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "0.75")
    budget = get_context_budget(200_000)
    assert budget == int(0.75 * 200_000)


def test_explicit_budget_is_clamped_to_safe_input_ceiling(monkeypatch):
    """Explicit fractions and absolute values cannot consume output headroom."""
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "1.0")
    assert get_context_budget(200_000, max_output=64_000, headroom=1_000) == 135_000

    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "500000")
    assert get_context_budget(200_000, max_output=64_000, headroom=1_000) == 135_000


def test_non_finite_env_budget_falls_back(monkeypatch):
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "inf")
    assert get_context_budget(200_000) == 180_000


def test_config_budget_resolution_prefers_project_over_user(monkeypatch):
    monkeypatch.delenv("GPTME_CONTEXT_BUDGET", raising=False)
    previous = get_config()
    try:
        set_config(
            Config(
                user=UserConfig(context=ContextConfig(budget=0.7)),
                project=ProjectConfig(context=ContextConfig(budget=0.8)),
            )
        )
        assert get_context_budget(200_000) == 160_000

        set_config(Config(user=UserConfig(context=ContextConfig(budget=0.7))))
        assert get_context_budget(200_000) == 140_000
    finally:
        set_config(previous)


def test_user_context_budget_is_loaded(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[context]\nbudget = 0.7\n", encoding="utf-8")

    config = load_user_config(str(config_path))

    assert config.context.budget == 0.7
    assert "[context]" in config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", [0, -1, 1.5, math.inf, math.nan])
def test_context_config_rejects_invalid_budget(value):
    with pytest.raises(ValueError, match="context.budget"):
        ContextConfig.from_dict({"budget": value})


def test_env_budget_invalid_ignored(monkeypatch):
    """Invalid GPTME_CONTEXT_BUDGET falls back to default."""
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "not-a-number")
    window = 200_000
    budget = get_context_budget(window)
    # Should fall back to the default computation
    assert budget < window
    assert budget > 0


def test_env_budget_zero_ignored(monkeypatch):
    """GPTME_CONTEXT_BUDGET=0 is not a valid fraction and falls through to default."""
    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "0")
    window = 200_000
    budget = get_context_budget(window)
    assert budget > 0
    assert budget < window


def test_large_window_uses_fraction():
    """For a 1M-window model the budget should be 0.9 × 1M = 900k."""
    window = 1_000_000
    budget = get_context_budget(window)
    # With max_output=8192, headroom=1000:
    # min(0.9 × 1M, 1M − 8192 − 1000) = min(900k, 990808) = 900k
    assert budget == int(0.9 * window)


def test_minimum_budget_clamp():
    """Very small windows produce at least 1000 tokens when safely possible."""
    budget = get_context_budget(1500, max_output=0, headroom=0)
    assert budget >= 1000


def test_minimum_budget_does_not_exceed_safe_ceiling():
    assert get_context_budget(1500, max_output=1000, headroom=100) == 400


def test_small_window_clamps_instead_of_raising():
    """A window that cannot cover max_output + headroom must not raise.

    Regression test: small local models (e.g. 8k Ollama/LM Studio models with
    no declared max_output) hit safe_ceiling <= 0, which used to raise
    ValueError before env/config resolution — silently disabling compaction
    for those models while every hook invocation logged a traceback.
    """
    # 8192 window with default max_output=8192 and headroom=1000: ceiling is
    # negative → clamped to the 1000-token floor instead of raising.
    assert get_context_budget(8192) == 1000


def test_small_window_env_override_still_resolves():
    """An explicit GPTME_CONTEXT_BUDGET must work even on clamped ceilings."""
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {"GPTME_CONTEXT_BUDGET": "0.5"}):
        assert get_context_budget(8192) == 1000
