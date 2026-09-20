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
    # 1. Environment variable override
    env_val = os.environ.get("GPTME_CONTEXT_BUDGET")
    if env_val:
        try:
            parsed = float(env_val)
            if parsed > 1:
                budget = int(parsed)
                logger.debug(
                    "Context budget from GPTME_CONTEXT_BUDGET: %d tokens", budget
                )
                return budget
            if 0 < parsed <= 1:
                budget = int(parsed * model_context)
                logger.debug(
                    "Context budget from GPTME_CONTEXT_BUDGET (%.2f × %d): %d tokens",
                    parsed,
                    model_context,
                    budget,
                )
                return budget
            logger.warning(
                "GPTME_CONTEXT_BUDGET=%r is not a valid fraction (0<x≤1) or "
                "absolute token count (>1); using default",
                env_val,
            )
        except ValueError:
            logger.warning(
                "GPTME_CONTEXT_BUDGET=%r is not a number; using default", env_val
            )

    # 2. Config-level budget
    try:
        from ..config import get_config  # fmt: skip

        cfg = get_config()
        budget_cfg = cfg.context.budget if hasattr(cfg, "context") else None
        if budget_cfg is not None:
            if isinstance(budget_cfg, float) and 0 < budget_cfg <= 1:
                budget = int(budget_cfg * model_context)
                logger.debug(
                    "Context budget from config (%.2f × %d): %d tokens",
                    budget_cfg,
                    model_context,
                    budget,
                )
                return budget
            if isinstance(budget_cfg, int) and budget_cfg > 1:
                logger.debug(
                    "Context budget from config (absolute): %d tokens", budget_cfg
                )
                return budget_cfg
    except Exception:
        pass  # Config not yet loaded; fall through to default

    # 3. Dynamic default
    budget = min(
        int(_DEFAULT_FRACTION * model_context),
        model_context - max_output - headroom,
    )
    # Clamp to a sensible minimum so tiny test models still trigger compaction.
    budget = max(budget, 1000)
    logger.debug(
        "Context budget (default): %d tokens (window=%d)", budget, model_context
    )
    return budget
