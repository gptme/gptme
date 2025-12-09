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

from dataclasses import dataclass

from ..util.cost_tracker import CostTracker, SessionCosts


@dataclass
class CostSummary:
    """Typed cost summary for eval results.

    Replaces untyped dict return, providing clear structure for eval integration.
    """

    session_id: str
    total_cost: float
    total_input_tokens: int
    total_output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_hit_rate: float
    request_count: int


def get_eval_costs() -> CostSummary | None:
    """Get cost summary for current eval run.

    Returns:
        CostSummary with cost metrics, or None if no session is active.
    """
    summary = CostTracker.get_summary()
    if not summary:
        return None
    return CostSummary(
        session_id=summary.get("session_id", ""),
        total_cost=summary.get("total_cost", 0.0),
        total_input_tokens=summary.get("total_input_tokens", 0),
        total_output_tokens=summary.get("total_output_tokens", 0),
        cache_read_tokens=summary.get("cache_read_tokens", 0),
        cache_creation_tokens=summary.get("cache_creation_tokens", 0),
        cache_hit_rate=summary.get("cache_hit_rate", 0.0),
        request_count=summary.get("request_count", 0),
    )


def get_session_costs() -> SessionCosts | None:
    """Get detailed session costs for eval analysis.

    Returns:
        SessionCosts object with per-request entries, or None if no session.
        Use this for detailed cost breakdowns by model, timing analysis, etc.
    """
    return CostTracker.get_session_costs()
