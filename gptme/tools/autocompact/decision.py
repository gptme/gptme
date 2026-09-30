"""Compaction decision logic — decides when and how to compact.

Provides estimation of potential savings and decision functions
for triggering auto-compaction.
"""

import logging
from typing import Literal

from ...llm.models import get_default_model, get_model
from ...message import Message, len_tokens
from ...util.context_budget import get_context_budget

logger = logging.getLogger(__name__)

# Minimum savings threshold - don't compact unless we can achieve meaningful savings
# This prevents triggering compaction that only saves a few percent (not worth cache invalidation)
MIN_SAVINGS_RATIO = 0.10  # Require at least 10% savings to justify compaction

# Hysteresis: after a budget-triggered compaction, trim toward this fraction of
# the budget rather than the trigger itself. Without headroom a trim that lands
# just below the budget re-triggers on the very next step, busting the cache
# again for a few percent of savings. Direct callers of the engine keep the
# previous behaviour unless they opt in (``target_ratio=1.0`` default).
TRIM_TARGET_RATIO = 0.7

CompactAction = Literal["none", "rule_based", "summarize"]


def estimate_compaction_savings(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int = 5,
    assistant_compression_age_threshold: int = 3,
    assistant_compression_min_tokens: int = 1000,
    keep_head: int = 0,
) -> tuple[int, int, int]:
    """
    Estimate potential savings from auto-compaction without actually compacting.

    Args:
        log: The conversation log to estimate savings for
        limit: Token limit (defaults to 90% of model context)
        max_tool_result_tokens: Threshold for "massive" tool results
        reasoning_strip_age_threshold: Distance from end before stripping reasoning
        assistant_compression_age_threshold: Distance from end before compressing assistant messages
        assistant_compression_min_tokens: Minimum tokens for assistant message compression

    Returns:
        Tuple of (total_tokens, estimated_savings, savings_from_reasoning)

    This allows us to decide if compaction is worth the cost (cache invalidation)
    before actually triggering it.

    Note: Estimation matches actual compaction logic:
    - Phase 0: Stale tool-output stubbing (only when over/close to limit)
    - Phase 1: Reasoning stripping (always applied to old messages)
    - Phase 2: Tool result removal (only when over/close to limit)
    - Phase 3: Assistant message compression (only when over/close to limit)
    """

    model = get_default_model() or get_model("gpt-4")
    total_tokens = len_tokens(log, model.model)

    if limit is None:
        limit = get_context_budget(
            model.context,
            max_output=model.max_output or 8192,
            model_id=model.full,
            model_context_budget=model.context_budget,
        )

    # The configured budget is the sole compaction trigger.
    would_remove_tool_results = total_tokens >= limit

    # Phase 0 runs first in auto_compact_log; estimate it on the original log
    # and run later-phase estimates on the stubbed log so we don't double-count
    # a 200–2000 token stale output as both a Phase 0 stub and a Phase 2 cut.
    # Do not pass logdir: estimation must not write tool-output files.
    # for_estimate=True uses the recovery-path template with a placeholder
    # so savings match the persisted form (short stubs overestimate).
    estimated_phase0_savings = 0
    work_log = log
    if would_remove_tool_results:
        from .engine import prune_stale_tool_outputs

        work_log, estimated_phase0_savings = prune_stale_tool_outputs(
            log, model.model, keep_head=keep_head, for_estimate=True
        )
        # Engine rechecks the budget after Phase 0 and skips Phase 2/3 when
        # stubbing already brought the log under limit. Match that here so we
        # don't count Phase 2 savings the engine would never take.
        would_remove_tool_results = len_tokens(work_log, model.model) >= limit

    log_length = len(work_log)
    estimated_tool_result_savings = 0
    # Age-based reasoning stripping is no longer part of the default compaction
    # path (it edits the provider-visible prefix before retained signed reasoning
    # blocks), so it contributes nothing to the estimate. The third return value
    # is kept at zero for API compatibility.
    estimated_reasoning_savings = 0
    estimated_compression_savings = 0

    for idx, msg in enumerate(work_log):
        if msg.pinned:
            continue

        msg_tokens = len_tokens(msg.content, model.model)
        distance_from_end = log_length - idx - 1

        # Phase 2: Estimate massive tool result removal savings
        # CRITICAL: Only count if we would actually remove tool results
        if (
            would_remove_tool_results
            and msg.role == "system"
            and msg_tokens > max_tool_result_tokens
        ):
            # Tool results get reduced to summary (~200 tokens)
            estimated_tool_result_savings += msg_tokens - 200

        # Phase 3: Estimate assistant message compression savings
        # Only for older messages (distance >= threshold) with enough tokens.
        # Think-bearing messages are skipped by the engine (rewriting them would
        # destroy the provider signature), so they must not count here either.
        if (
            would_remove_tool_results
            and distance_from_end >= assistant_compression_age_threshold
            and msg.role == "assistant"
            and "<think>" not in msg.content
            and "<thinking>" not in msg.content
            and msg_tokens > assistant_compression_min_tokens
        ):
            # Compression targets 70% of original, so saves ~30%
            estimated_compression_savings += int(msg_tokens * 0.3)

    total_estimated_savings = (
        estimated_phase0_savings
        + estimated_tool_result_savings
        + estimated_reasoning_savings
        + estimated_compression_savings
    )
    return total_tokens, total_estimated_savings, estimated_reasoning_savings


def should_auto_compact(
    log: list[Message],
    limit: int | None = None,
    keep_head: int = 0,
) -> CompactAction:
    """
    Check if a log should be auto-compacted.

    Returns:
        "rule_based" if the log would benefit from rule-based auto-compacting,
        "summarize" if the conversation is over-limit but rule-based savings are too low
            (LLM-powered summarization should be used instead),
        "none" if no compaction is needed.

    Auto-compacting is triggered when the conversation reaches the resolved budget.
    Rule-based compaction is selected only when estimated savings exceed
    ``MIN_SAVINGS_RATIO``; otherwise the LLM summarizer handles the same trigger.
    """

    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = get_context_budget(
            model.context,
            max_output=model.max_output or 8192,
            model_id=model.full,
            model_context_budget=model.context_budget,
        )

    total_tokens = len_tokens(log, model.model)

    # One budget means one trigger: no separate provider-window fraction.
    would_trigger = total_tokens >= limit

    if not would_trigger:
        return "none"

    # Second check: estimate if savings would be worth it
    total, estimated_savings, reasoning_savings = estimate_compaction_savings(
        log, limit, keep_head=keep_head
    )
    savings_ratio = estimated_savings / total if total > 0 else 0

    if savings_ratio < MIN_SAVINGS_RATIO:
        logger.info(
            f"Skipping rule-based auto-compact: estimated savings {savings_ratio:.1%} "
            f"({estimated_savings} tokens) below threshold {MIN_SAVINGS_RATIO:.0%}. "
            f"Will use LLM-powered summarization instead."
        )
        return "summarize"

    logger.debug(
        f"Auto-compact viable: estimated {savings_ratio:.1%} savings "
        f"({estimated_savings} tokens, {reasoning_savings} from reasoning)"
    )
    return "rule_based"
