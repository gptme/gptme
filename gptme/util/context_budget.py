"""Context budget computation.

The *context budget* is the token count at which compaction is triggered.
It is deliberately distinct from the provider *context window* (what the API
accepts) and from the *max_output* tokens.

Resolution order (first match wins):
1. ``GPTME_CONTEXT_BUDGET`` env var — fraction or absolute
2. ``[context] budget`` in project/user config
3. Dynamic default: ``min(0.9 × window, window − max_output − headroom)``

Why keep it separate from the window:
- 1M-window models never hit the 50% autocompact trigger that works for 200k
  models, because the raw threshold is 524k tokens, which sessions rarely reach.
- Operators need a knob that means "start compacting here" without changing
  the underlying model's declared context window.
"""

import logging
import math
import os

logger = logging.getLogger(__name__)

# Default fraction of the model window used when no explicit budget is set.
# Chosen to match today's behaviour for 200k models (~167k at 90%) and to
# match Claude Code / Codex / OpenCode patterns (all use ~0.85-0.90).
_DEFAULT_FRACTION = 0.9

# Reserve this many tokens for model output and internal headroom even when an
# explicit budget fraction would leave less room.
_DEFAULT_MAX_OUTPUT = 8192
_DEFAULT_HEADROOM = 1000

# Floor for the budget on tiny windows where reserving output tokens and
# headroom leaves nothing: clamp instead of raising so compaction stays
# available (and env/config overrides still resolve) for small local models.
_MIN_BUDGET = 1000


def get_context_budget(
    model_context: int,
    *,
    max_output: int = _DEFAULT_MAX_OUTPUT,
    headroom: int = _DEFAULT_HEADROOM,
) -> int:
    """Return the context budget in tokens for the given model window.

    Args:
        model_context: The model's declared context window in tokens.
        max_output: Reserved tokens for model output (default 8192).
        headroom: Additional safety margin (default 1000).

    Returns:
        Token count at which compaction should be triggered.
    """
    safe_ceiling = model_context - max_output - headroom
    if safe_ceiling <= 0:
        logger.warning(
            "Model context %d does not exceed reserved output (%d) + headroom "
            "(%d); clamping context budget to minimum %d",
            model_context,
            max_output,
            headroom,
            _MIN_BUDGET,
        )
        safe_ceiling = _MIN_BUDGET

    def resolve(value: float | int) -> int | None:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        if parsed <= 1:
            return min(int(parsed * model_context), safe_ceiling)
        if parsed.is_integer():
            return min(int(parsed), safe_ceiling)
        return None

    # 1. Environment variable override
    env_val = os.environ.get("GPTME_CONTEXT_BUDGET")
    if env_val:
        try:
            budget = resolve(float(env_val))
        except ValueError:
            budget = None
        if budget is not None:
            logger.debug("Context budget from GPTME_CONTEXT_BUDGET: %d tokens", budget)
            return min(max(budget, 1000), safe_ceiling)
        logger.warning(
            "GPTME_CONTEXT_BUDGET=%r is not a valid fraction (0<x≤1) or "
            "absolute token count (>1); using config/default",
            env_val,
        )

    # 2. Config-level budget (project overrides user).
    from ..config import get_config  # fmt: skip

    cfg = get_config()
    budget_cfg = (
        cfg.project.context.budget
        if cfg.project is not None and cfg.project.context.budget is not None
        else cfg.user.context.budget
    )
    if budget_cfg is not None:
        budget = resolve(budget_cfg)
        if budget is not None:
            logger.debug("Context budget from config: %d tokens", budget)
            return min(max(budget, 1000), safe_ceiling)

    # 3. Dynamic default
    budget = min(int(_DEFAULT_FRACTION * model_context), safe_ceiling)
    # Prefer a 1000-token minimum when the safe ceiling permits it.
    budget = min(max(budget, 1000), safe_ceiling)
    logger.debug(
        "Context budget (default): %d tokens (window=%d)", budget, model_context
    )
    return budget
