"""Compaction decision logic — decides when and how to compact.

Provides estimation of potential savings and decision functions
for triggering auto-compaction.
"""

import logging
from typing import Literal

from ...llm.models import get_default_model, get_model
from ...message import Message, len_tokens

logger = logging.getLogger(__name__)

# Minimum savings threshold - don't compact unless we can achieve meaningful savings
# This prevents triggering compaction that only saves a few percent (not worth cache invalidation)
MIN_SAVINGS_RATIO = 0.10  # Require at least 10% savings to justify compaction

CompactAction = Literal["none", "rule_based", "summarize"]


def estimate_compaction_savings(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int = 5,
    assistant_compression_age_threshold: int = 3,
    assistant_compression_min_tokens: int = 1000,
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
    - Phase 1: Reasoning stripping (always applied to old messages)
    - Phase 2: Tool result removal (only when over/close to limit)
    - Phase 3: Assistant message compression (only when over/close to limit)
    """

    model = get_default_model() or get_model("gpt-4")
    total_tokens = len_tokens(log, model.model)
    log_length = len(log)

    if limit is None:
        limit = int(0.9 * model.context)

    # Match actual compaction logic: only remove tool results when over/close to limit
    close_to_limit = total_tokens >= int(0.8 * model.context)
    would_remove_tool_results = total_tokens > limit or close_to_limit

    estimated_tool_result_savings = 0
    estimated_reasoning_savings = 0
    estimated_compression_savings = 0

    for idx, msg in enumerate(log):
        if msg.pinned:
            continue

        msg_tokens = len_tokens(msg.content, model.model)
        distance_from_end = log_length - idx - 1

        # Phase 1: Estimate reasoning stripping savings (always applied to old messages)
        if distance_from_end >= reasoning_strip_age_threshold:
            if "<think>" in msg.content or "<thinking>" in msg.content:
                # Rough estimate: reasoning usually ~30-50% of content
                estimated_reasoning_savings += int(msg_tokens * 0.3)

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
        # Only for older messages (distance >= threshold) with enough tokens
        if (
            would_remove_tool_results
            and distance_from_end >= assistant_compression_age_threshold
            and msg.role == "assistant"
            and msg_tokens > assistant_compression_min_tokens
        ):
            # Compression targets 70% of original, so saves ~30%
            estimated_compression_savings += int(msg_tokens * 0.3)

    total_estimated_savings = (
        estimated_tool_result_savings
        + estimated_reasoning_savings
        + estimated_compression_savings
    )
    return total_tokens, total_estimated_savings, estimated_reasoning_savings


def should_auto_compact(log: list[Message], limit: int | None = None) -> CompactAction:
    """
    Check if a log should be auto-compacted.

    Returns:
        "rule_based" if the log would benefit from rule-based auto-compacting,
        "summarize" if the conversation is over-limit but rule-based savings are too low
            (LLM-powered summarization should be used instead),
        "none" if no compaction is needed.

    Auto-compacting is triggered when:
    1. The conversation exceeds the limit, OR
    2. The conversation is close to the limit (80%+) AND contains massive tool results
    3. AND estimated savings exceed MIN_SAVINGS_RATIO (to justify cache invalidation)
    """

    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.9 * model.context)

    total_tokens = len_tokens(log, model.model)
    close_to_limit = total_tokens >= int(
        0.5 * model.context
    )  # 50% threshold (more proactive)

    # Check if there are any massive system messages (tool results)
    has_massive_tool_result = False
    for msg in log:
        if not msg.pinned and msg.role == "system":
            msg_tokens = len_tokens(msg.content, model.model)
            if msg_tokens > 2000:  # Threshold for "massive"
                has_massive_tool_result = True
                break

    # First check: would we trigger based on token count?
    would_trigger = total_tokens > limit or (close_to_limit and has_massive_tool_result)

    if not would_trigger:
        return "none"

    # Second check: estimate if savings would be worth it
    total, estimated_savings, reasoning_savings = estimate_compaction_savings(
        log, limit
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
