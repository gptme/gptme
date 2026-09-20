"""Tests for the context budget computation (Phase 1 of #3812)."""

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
    """Very small windows produce at least 1000 tokens (minimum clamp)."""
    budget = get_context_budget(1500, max_output=0, headroom=0)
    assert budget >= 1000
