"""Cost tracking integration for eval framework.

Provides functions for evals to access cost data from completed runs.

Usage in evals:
    from gptme.eval.cost import get_eval_costs, get_session_costs

    # Get summary dict for logging/reporting
    costs = get_eval_costs()
    print(f"Eval cost: ${costs.get('total_cost', 0):.4f}")

    # Get detailed costs for analysis
    session_costs = get_session_costs()
    if session_costs:
        for entry in session_costs.entries:
            print(f"  {entry.model}: ${entry.cost:.4f}")
"""

from ..util.cost_tracker import CostTracker, SessionCosts


def get_eval_costs() -> dict:
    """Get cost summary for current eval run.

    Returns:
        Dictionary with cost metrics:
        - session_id: str
        - total_cost: float
        - total_input_tokens: int
        - total_output_tokens: int
        - cache_read_tokens: int
        - cache_creation_tokens: int
        - cache_hit_rate: float (0.0-1.0)
        - request_count: int

        Empty dict if no session is active.
    """
    return CostTracker.get_summary()


def get_session_costs() -> SessionCosts | None:
    """Get detailed session costs for eval analysis.

    Returns:
        SessionCosts object with per-request entries, or None if no session.
        Use this for detailed cost breakdowns by model, timing analysis, etc.
    """
    return CostTracker.get_session_costs()
