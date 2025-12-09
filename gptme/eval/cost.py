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

from ..util.cost_tracker import CostSummary, CostTracker, SessionCosts

# Re-export CostSummary for backward compatibility
__all__ = ["CostSummary", "get_eval_costs", "get_session_costs"]


def get_eval_costs() -> CostSummary | None:
    """Get cost summary for current eval run.

    Returns:
        CostSummary with cost metrics, or None if no session is active.
    """
    return CostTracker.get_summary()


def get_session_costs() -> SessionCosts | None:
    """Get detailed session costs for eval analysis.

    Returns:
        SessionCosts object with per-request entries, or None if no session.
        Use this for detailed cost breakdowns by model, timing analysis, etc.
    """
    return CostTracker.get_session_costs()
