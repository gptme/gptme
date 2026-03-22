"""Rule-based compaction engine — the 3-phase compaction algorithm.

Implements strategic removal of content from conversations:
1. Strip reasoning tags from older messages (age-based)
2. Truncate largest tool results first (oh-my-opencode strategy)
3. Extractive compression for long assistant messages
"""

import logging
from collections.abc import Generator
from pathlib import Path

from ...context import strip_reasoning
from ...llm.models import get_default_model, get_model
from ...message import Message, len_tokens
from ...util.master_context import (
    MessageByteRange,
    build_master_context_index,
    create_master_context_reference,
)
from ...util.output_storage import create_tool_result_summary
from ...util.reduce import reduce_log
from .scoring import compress_content

logger = logging.getLogger(__name__)


def auto_compact_log(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
    reasoning_strip_age_threshold: int = 5,
    logdir: Path | None = None,
) -> Generator[Message, None, None]:
    """
    Auto-compact log for conversations with massive tool results.

    More aggressive than reduce_log - implements strategic removal:
    1. Strips reasoning tags from older messages (age-based)
    2. Removes massive tool results (largest-first strategy for efficiency)
    3. Extractive compression for long assistant messages (Phase 3)

    The "truncate largest first" strategy (inspired by oh-my-opencode) ensures we
    achieve target token reduction with minimal information loss by prioritizing
    the largest outputs for truncation.

    Master Context Architecture:
    The original conversation.jsonl serves as the master context - an append-only
    log that is never compacted. When truncating content, we include byte range
    references to the master context for exact recovery. This allows aggressive
    compaction while preserving full context accessibility.

    Args:
        log: List of messages to compact
        limit: Token limit (defaults to 80% of model context)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
        reasoning_strip_age_threshold: Strip reasoning from messages >N positions back
        logdir: Path to conversation directory for saving removed outputs
    """

    # Build master context index for byte-range references
    master_context_index: list[MessageByteRange] = []
    master_logfile: Path | None = None
    if logdir:
        master_logfile = logdir / "conversation.jsonl"
        if master_logfile.exists():
            master_context_index = build_master_context_index(master_logfile)
            logger.debug(
                f"Built master context index with {len(master_context_index)} entries"
            )

    # get the token limit
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.8 * model.context)

    # if we are below the limit AND don't need compacting, return the log as-is
    tokens = len_tokens(log, model=model.model)
    close_to_limit = tokens >= int(0.7 * model.context)

    # Calculate message positions from end (for age-based reasoning stripping)
    log_length = len(log)

    # Check if any reasoning stripping is needed
    needs_reasoning_strip = any(
        (log_length - idx - 1) >= reasoning_strip_age_threshold
        and ("<think>" in msg.content or "<thinking>" in msg.content)
        for idx, msg in enumerate(log)
    )
    needs_compacting = tokens > limit or close_to_limit

    # Check if any messages need Phase 3 compression
    needs_phase3_compression = any(
        (log_length - idx - 1) >= 3  # Don't compress very recent messages
        and msg.role == "assistant"  # Only compress assistant responses
        and len_tokens(msg.content, model.model) > 1000  # Only compress long messages
        for idx, msg in enumerate(log)
    )

    # Only return early if nothing needs processing
    if (
        not needs_reasoning_strip
        and not needs_compacting
        and not needs_phase3_compression
    ):
        yield from log
        return

    if needs_compacting:
        logger.info(f"Auto-compacting log: {tokens} tokens exceeds limit of {limit}")
    if needs_reasoning_strip:
        logger.info(
            f"Stripping reasoning from messages beyond threshold {reasoning_strip_age_threshold}"
        )

    # Phase 1: Strategic reasoning stripping for older messages
    # Process all messages first to strip reasoning
    compacted_log: list[Message] = []
    reasoning_tokens_saved = 0

    for idx, msg in enumerate(log):
        if msg.pinned:
            compacted_log.append(msg)
            continue

        distance_from_end = log_length - idx - 1

        # Strip reasoning from messages beyond the threshold
        if distance_from_end >= reasoning_strip_age_threshold:
            stripped_content, reasoning_saved = strip_reasoning(
                msg.content, model.model
            )
            if reasoning_saved > 0:
                msg = msg.replace(content=stripped_content)
                reasoning_tokens_saved += reasoning_saved
                logger.debug(
                    f"Stripped reasoning from message {idx}: "
                    f"saved {reasoning_saved} tokens (distance from end: {distance_from_end})"
                )

        compacted_log.append(msg)

    # Phase 2: Truncate largest tool results first (oh-my-opencode strategy)
    # Instead of truncating all large results in order, prioritize the largest
    # This achieves target reduction with minimal information loss
    tool_result_tokens_saved = 0
    compression_tokens_saved = 0
    current_tokens = len_tokens(compacted_log, model.model)
    target_tokens = int(0.8 * model.context)  # Target 80% of context

    if current_tokens > target_tokens:
        # Identify all candidate tool results for truncation (with original indices)
        candidates: list[tuple[int, int, Message]] = []  # (idx, tokens, msg)
        for idx, msg in enumerate(compacted_log):
            if msg.pinned:
                continue
            if msg.role == "system":
                msg_tokens = len_tokens(msg.content, model.model)
                if msg_tokens > max_tool_result_tokens:
                    candidates.append((idx, msg_tokens, msg))

        # Sort by token count descending (largest first)
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Truncate largest outputs until under target
        for idx, msg_tokens, msg in candidates:
            if current_tokens <= target_tokens:
                logger.info(
                    f"Reached target tokens ({current_tokens} <= {target_tokens}), "
                    f"stopping truncation early"
                )
                break

            # Replace with a brief summary message
            summary_content = create_tool_result_summary(
                content=msg.content,
                original_tokens=msg_tokens,
                logdir=logdir,
                tool_name="autocompact",
            )

            # Add master context reference for exact recovery
            # Note: idx must match the message position in conversation.jsonl
            # This is safe because Phase 1-2 preserve message positions (1:1 mapping)
            if master_logfile and idx < len(master_context_index):
                byte_range = master_context_index[idx]
                # Get first line as preview
                preview = msg.content.split("\n")[0] if msg.content else None
                master_ref = create_master_context_reference(
                    logfile=master_logfile,
                    byte_range=byte_range,
                    original_tokens=msg_tokens,
                    preview=preview,
                )
                summary_content += f"\n\n{master_ref}"
                logger.debug(
                    f"Added master context reference for idx {idx}: "
                    f"bytes {byte_range.byte_start}-{byte_range.byte_end}"
                )

            summary_msg = msg.replace(content=summary_content)
            compacted_log[idx] = summary_msg

            saved = msg_tokens - len_tokens(summary_content, model.model)
            tool_result_tokens_saved += saved
            current_tokens -= saved
            logger.debug(
                f"Truncated largest tool result at idx {idx}: "
                f"{msg_tokens} -> {len_tokens(summary_content, model.model)} tokens "
                f"(saved {saved}, now at {current_tokens} tokens)"
            )

    # Phase 3: Extractive compression for long assistant messages
    compacted_log_len = len(compacted_log)
    for idx, msg in enumerate(compacted_log):
        if msg.pinned:
            continue

        distance_from_end = compacted_log_len - idx - 1
        msg_tokens = len_tokens(msg.content, model.model)

        # Compress messages >1000 tokens that aren't very recent
        if (
            distance_from_end >= 3  # Don't compress very recent messages
            and msg.role == "assistant"  # Only compress assistant responses
            and msg_tokens > 1000  # Only compress long messages
        ):
            compressed_content = compress_content(msg.content, target_ratio=0.7)
            compressed_tokens = len_tokens(compressed_content, model.model)

            if compressed_tokens < msg_tokens:
                # Add master context reference for exact recovery
                # Note: idx must match the message position in conversation.jsonl
                # This is safe because Phases 1-3 preserve message positions (1:1 mapping)
                if master_logfile and idx < len(master_context_index):
                    byte_range = master_context_index[idx]
                    # Get first line as preview
                    preview = msg.content.split("\n")[0] if msg.content else None
                    master_ref = create_master_context_reference(
                        logfile=master_logfile,
                        byte_range=byte_range,
                        original_tokens=msg_tokens,
                        preview=preview,
                    )
                    compressed_content += f"\n\n{master_ref}"
                    logger.debug(
                        f"Added master context reference for compressed idx {idx}"
                    )

                compacted_log[idx] = msg.replace(content=compressed_content)
                compression_saved = msg_tokens - compressed_tokens
                compression_tokens_saved += compression_saved
                logger.debug(
                    f"Compressed message {idx}: {msg_tokens} -> {compressed_tokens} tokens "
                    f"({compression_saved} saved, {(compression_saved / msg_tokens) * 100:.1f}% reduction)"
                )

    # Check if we're now within limits
    final_tokens = len_tokens(compacted_log, model.model)
    total_saved = (
        tool_result_tokens_saved + compression_tokens_saved + reasoning_tokens_saved
    )
    if final_tokens <= limit:
        # Calculate reduction percentage
        reduction_pct = ((tokens - final_tokens) / tokens * 100) if tokens > 0 else 0.0

        # Build detailed breakdown message
        breakdown_parts = []
        if reasoning_tokens_saved > 0:
            pct = (reasoning_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            breakdown_parts.append(
                f"reasoning: {reasoning_tokens_saved:,} ({pct:.0f}%)"
            )
        if tool_result_tokens_saved > 0:
            pct = (
                (tool_result_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            )
            breakdown_parts.append(
                f"tool results: {tool_result_tokens_saved:,} ({pct:.0f}%)"
            )
        if compression_tokens_saved > 0:
            pct = (
                (compression_tokens_saved / total_saved * 100) if total_saved > 0 else 0
            )
            breakdown_parts.append(
                f"compression: {compression_tokens_saved:,} ({pct:.0f}%)"
            )

        breakdown_str = ", ".join(breakdown_parts) if breakdown_parts else "no savings"
        logger.info(
            f"Auto-compacting successful: {tokens:,} -> {final_tokens:,} tokens "
            f"({reduction_pct:.1f}% reduction, saved {total_saved:,} tokens) "
            f"[{breakdown_str}]"
        )
        yield from compacted_log
        return

    # If still over limit, fall back to regular reduction
    logger.info("Auto-compacting not sufficient, falling back to regular reduction")

    yield from reduce_log(compacted_log, limit)
