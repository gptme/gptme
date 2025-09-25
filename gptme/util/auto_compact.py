"""
Auto-compacting utilities for handling conversations with massive tool results.

Implements more aggressive compacting than reduce.py for cases where tool results
are so large they prevent conversation resumption.
"""

import logging
from collections.abc import Generator

from ..message import Message, len_tokens
from ..llm.models import get_default_model, get_model

logger = logging.getLogger(__name__)


def auto_compact_log(
    log: list[Message],
    limit: int | None = None,
    max_tool_result_tokens: int = 2000,
) -> Generator[Message, None, None]:
    """
    Auto-compact log for conversations with massive tool results.

    More aggressive than reduce_log - completely removes massive tool results
    instead of just truncating them, to allow conversation resumption.

    Args:
        log: List of messages to compact
        limit: Token limit (defaults to 90% of model context)
        max_tool_result_tokens: Maximum tokens allowed in a tool result before removal
    """
    # get the token limit
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.9 * model.context)

    # if we are below the limit AND don't need compacting, return the log as-is
    tokens = len_tokens(log, model=model.model)
    close_to_limit = tokens >= int(0.8 * model.context)

    # Only return early if we're not close to limit and don't have massive tool results
    if tokens <= limit and not close_to_limit:
        yield from log
        return

    logger.info(f"Auto-compacting log: {tokens} tokens exceeds limit of {limit}")

    # Process messages and remove massive tool results
    compacted_log = []
    tokens_saved = 0

    for msg in log:
        # Skip processing pinned messages
        if msg.pinned:
            compacted_log.append(msg)
            continue

        msg_tokens = len_tokens(msg.content, model.model)

        # Check if this is a massive tool result (system message with huge content)
        # Use same logic as should_auto_compact: over limit OR close to limit with massive tool result
        close_to_limit = tokens >= int(0.8 * model.context)
        if (
            msg.role == "system"
            and msg_tokens > max_tool_result_tokens
            and (tokens > limit or close_to_limit)
        ):
            # Replace with a brief summary message
            summary_content = _create_tool_result_summary(msg.content, msg_tokens)
            summary_msg = msg.replace(content=summary_content)
            compacted_log.append(summary_msg)

            tokens_saved += msg_tokens - len_tokens(summary_content, model.model)
            logger.info(
                f"Removed massive tool result: {msg_tokens} tokens -> {len_tokens(summary_content, model.model)} tokens"
            )
        else:
            compacted_log.append(msg)

    # Check if we're now within limits
    final_tokens = len_tokens(compacted_log, model.model)
    if final_tokens <= limit:
        logger.info(
            f"Auto-compacting successful: {tokens} -> {final_tokens} tokens (saved {tokens_saved})"
        )
        yield from compacted_log
        return

    # If still over limit, fall back to regular reduction
    logger.info("Auto-compacting not sufficient, falling back to regular reduction")
    from .reduce import reduce_log

    yield from reduce_log(compacted_log, limit)


def _create_tool_result_summary(content: str, original_tokens: int) -> str:
    """
    Create a brief summary message to replace a massive tool result.

    Args:
        content: Original tool result content
        original_tokens: Number of tokens in original content

    Returns:
        Brief summary message
    """
    # Try to extract the command that was run from the content
    lines = content.split("\n")
    command_info = ""

    # Look for common tool execution patterns
    for line in lines[:10]:  # Check first 10 lines
        if (
            line.startswith("Ran command:")
            or line.startswith("Executed:")
            or "shell" in line.lower()
        ):
            command_info = f" ({line.strip()})"
            break

    # Check if this was likely a successful or failed execution
    status = "completed"
    if any(
        word in content.lower()
        for word in ["error", "failed", "exception", "traceback"]
    ):
        status = "failed"

    return f"[Large tool output removed - {original_tokens} tokens]: Tool execution {status}{command_info}. Output was automatically removed due to size to allow conversation continuation."


def should_auto_compact(log: list[Message], limit: int | None = None) -> bool:
    """
    Check if a log should be auto-compacted.

    Returns True if the log contains massive tool results that would benefit
    from auto-compacting rather than regular reduction.

    Auto-compacting is triggered when:
    1. The conversation exceeds the limit, OR
    2. The conversation is close to the limit (80%+) AND contains massive tool results
    """
    model = get_default_model() or get_model("gpt-4")
    if limit is None:
        limit = int(0.9 * model.context)

    total_tokens = len_tokens(log, model.model)
    close_to_limit = total_tokens >= int(0.8 * model.context)  # 80% threshold

    # Check if there are any massive system messages (tool results)
    has_massive_tool_result = False
    for msg in log:
        if not msg.pinned and msg.role == "system":
            msg_tokens = len_tokens(msg.content, model.model)
            if msg_tokens > 2000:  # Threshold for "massive"
                has_massive_tool_result = True
                break

    # Trigger auto-compacting if over limit OR close to limit with massive tool results
    return total_tokens > limit or (close_to_limit and has_massive_tool_result)
