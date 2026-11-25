"""Context budget computation.

The *context budget* is the token count at which compaction is triggered.
It is deliberately distinct from the provider *context window* (what the API
accepts) and from the *max_output* tokens.

Resolution order (first match wins):
1. Per-chat CLI override persisted by ``--context-budget``
2. ``GPTME_CONTEXT_BUDGET`` env var — fraction or absolute
3. Per-model ``[models."<id>"] context_budget``
4. ``[context] budget`` in project/user config (legacy broad override)
5. Model metadata ``context_budget``
6. Capped default: ``min(0.9 × window, window − max_output − headroom, 256k)``

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
DEFAULT_MAX_OUTPUT = 8192
DEFAULT_HEADROOM = 1000
_DEFAULT_LONG_WINDOW_CAP = 256_000

# Floor for the budget on tiny windows where reserving output tokens and
# headroom leaves nothing: clamp instead of raising so compaction stays
# available (and env/config overrides still resolve) for small local models.
MIN_BUDGET = 1000


def get_context_budget(
    model_context: int,
    *,
    max_output: int = DEFAULT_MAX_OUTPUT,
    headroom: int = DEFAULT_HEADROOM,
    model_id: str | None = None,
    model_context_budget: float | int | None = None,
) -> int:
    """Return the context budget in tokens for the given model window.

    Args:
        model_context: The model's declared context window in tokens.
        max_output: Model output allowance (default 8192). Without model_id,
            treated as a reservation and never reduced.
        headroom: Additional safety margin (default 1000).
        model_id: Fully-qualified model ID used for per-model config lookup.
        model_context_budget: Budget declared by built-in model metadata.

    Returns:
        Token count at which compaction should be triggered.
    """
    from ..config import get_config  # fmt: skip

    cfg = get_config()
    request_output = cfg.chat.max_tokens if cfg.chat is not None else None
    if model_id is not None:
        from ..llm import _resolve_max_tokens  # fmt: skip

        # Use the same CLI/chat > provider env/config > default precedence as
        # the outgoing request. Never shrink a real request reservation.
        request_output = _resolve_max_tokens(model_id, request_output)
        # Some providers advertise max_output as nearly the whole window,
        # meaning "output may use whatever the prompt leaves". Reserving all
        # 943,718 advertised tokens on DeepSeek's 1,048,576 window left ~104k
        # for input and caused compaction every few tool calls.
        # Only reduce metadata when we know the actual request allowance;
        # other providers may fill in their own max_tokens from metadata.
        # If the metadata allowance and local headroom leave no room for
        # input, bound the reserve used for compaction too, without changing
        # the outgoing request. xai, moonshot and Codex models advertise
        # max_output == context and would otherwise clamp the budget to 1000,
        # compacting every turn.
        leaves_no_input = max_output + headroom >= model_context
        if (
            request_output is not None or leaves_no_input
        ) and max_output > model_context // 2:
            max_output = min(max_output, max(DEFAULT_MAX_OUTPUT, model_context // 8))
    if request_output is not None:
        max_output = max(max_output, request_output)
    safe_ceiling = model_context - max_output - headroom
    if safe_ceiling <= 0:
        # Clamp instead of raising so env/config overrides still resolve on
        # small local models. Never clamp above the model window itself: a
        # 1000-token floor on a 500-token model would make the budget larger
        # than the window, so compaction would never fire before the provider
        # rejects the request.
        safe_ceiling = max(1, min(MIN_BUDGET, model_context))
        logger.warning(
            "Model context %d does not exceed reserved output (%d) + headroom "
            "(%d); clamping context budget to %d",
            model_context,
            max_output,
            headroom,
            safe_ceiling,
        )

    def resolve(value: float | int) -> int | None:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        if parsed <= 1:
            return min(int(parsed * model_context), safe_ceiling)
        if parsed.is_integer():
            return min(int(parsed), safe_ceiling)
        return None

    # 1. One-chat CLI override (persisted in the conversation config).
    if cfg.chat is not None and cfg.chat.context_budget is not None:
        budget = resolve(cfg.chat.context_budget)
        if budget is not None:
            logger.debug("Context budget from CLI/chat config: %d tokens", budget)
            return min(budget, safe_ceiling)

    # 2. Environment variable override.
    env_val = os.environ.get("GPTME_CONTEXT_BUDGET")
    if env_val:
        try:
            budget = resolve(float(env_val))
        except ValueError:
            budget = None
        if budget is not None:
            logger.debug("Context budget from GPTME_CONTEXT_BUDGET: %d tokens", budget)
            # Explicit overrides are never raised above their resolved value:
            # the 1000-token minimum is a default-path preference, not a
            # correction of operator intent (a 0.5 fraction on a 500-token
            # window must stay 250, not be clamped up to the ceiling).
            return min(budget, safe_ceiling)
        logger.warning(
            "GPTME_CONTEXT_BUDGET=%r is not a valid fraction (0<x≤1) or "
            "absolute token count (>1); using config/default",
            env_val,
        )

    # 3. Per-model user config.
    if model_id is not None:
        model_budget = cfg.user.models.get_context_budget(model_id)
        if model_budget is not None:
            budget = resolve(model_budget)
            if budget is not None:
                logger.debug(
                    "Context budget from config for %s: %d tokens", model_id, budget
                )
                return min(budget, safe_ceiling)

    # 4. Broad config-level budget (project overrides user).
    budget_cfg = (
        cfg.project.context.budget
        if cfg.project is not None and cfg.project.context.budget is not None
        else cfg.user.context.budget
    )
    if budget_cfg is not None:
        budget = resolve(budget_cfg)
        if budget is not None:
            logger.debug("Context budget from config: %d tokens", budget)
            # Same rule as the env override: respect the resolved value.
            return min(budget, safe_ceiling)

    # 5. Model metadata (used to opt validated long-window models out of the cap).
    if model_context_budget is not None:
        budget = resolve(model_context_budget)
        if budget is not None:
            logger.debug("Context budget from model metadata: %d tokens", budget)
            return min(budget, safe_ceiling)

    # 6. Conservative default. The absolute cap avoids replaying degraded
    # long contexts unless a model has an explicit, evidence-backed override.
    budget = min(
        int(_DEFAULT_FRACTION * model_context),
        safe_ceiling,
        _DEFAULT_LONG_WINDOW_CAP,
    )
    # Prefer a 1000-token minimum when the safe ceiling permits it.
    budget = min(max(budget, 1000), safe_ceiling)
    logger.debug(
        "Context budget (default): %d tokens (window=%d)", budget, model_context
    )
    return budget
