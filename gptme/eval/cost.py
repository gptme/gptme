"""Cost tracking integration for eval framework.

Provides functions for evals to access cost data from completed runs.

Usage in evals:
    from gptme.eval.cost import get_eval_costs, get_session_costs, CostSummary

    # Get typed cost summary for logging/reporting
    costs = get_eval_costs()
    if costs:
        print(f"Eval cost: ${costs.total_cost:.4f}")

    # Get detailed costs for analysis
    session_costs = get_session_costs()
    if session_costs:
        for entry in session_costs.entries:
            print(f"  {entry.model}: ${entry.cost:.4f}")
"""

from typing import TypedDict

from ..util.cost_tracker import CostSummary, CostTracker, SessionCosts


class EvalTokenFields(TypedDict):
    tokens_input: int
    tokens_output: int
    cost_usd: float | None
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float
    num_steps: int


# Re-export CostSummary for backward compatibility
__all__ = [
    "CostSummary",
    "get_eval_costs",
    "get_session_costs",
    "token_fields_from_cost",
]


def get_eval_costs() -> CostSummary | None:
    """Get cost summary for current eval run.

    Returns:
        CostSummary with cost metrics, or None if no session is active.
    """
    return CostTracker.get_summary()


def token_fields_from_cost(cost: CostSummary | None) -> EvalTokenFields:
    """Map CostSummary to EvalResult token/cost field values."""
    if not cost:
        return {
            "tokens_input": 0,
            "tokens_output": 0,
            "cost_usd": None,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_hit_rate": 0.0,
            "num_steps": 0,
        }
    return {
        "tokens_input": cost.total_input_tokens,
        "tokens_output": cost.total_output_tokens,
        "cost_usd": cost.total_cost,
        "cache_read_tokens": cost.cache_read_tokens,
        "cache_creation_tokens": cost.cache_creation_tokens,
        "cache_hit_rate": cost.cache_hit_rate,
        "num_steps": cost.request_count,
    }


def get_session_costs() -> SessionCosts | None:
    """Get detailed session costs for eval analysis.

    Returns:
        SessionCosts object with per-request entries, or None if no session.
        Use this for detailed cost breakdowns by model, timing analysis, etc.
    """
    return CostTracker.get_session_costs()
