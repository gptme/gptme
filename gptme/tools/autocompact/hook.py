"""Hook implementation and tool registration for auto-compacting.

Provides the auto-trigger hook that runs after each message and
the ToolSpec that registers the tool with the framework.
"""

import logging
import re
import time
from collections.abc import Generator
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ...hooks import HookType, StopPropagation, trigger_hook
from ...llm.models import get_default_model
from ...message import Message, len_tokens
from ..base import ToolSpec
from .decision import should_auto_compact
from .engine import auto_compact_log
from .handlers import cmd_compact_handler
from .resume import _resume_via_llm

if TYPE_CHECKING:
    from ...logmanager import LogManager

logger = logging.getLogger(__name__)

# Reentrancy guard to prevent infinite loops
_last_autocompact_time = 0.0
_autocompact_min_interval = 60  # Minimum 60 seconds between autocompact attempts


def _get_compacted_name(conversation_name: str) -> str:
    """
    Get a unique name for the compacted conversation fork.

    The original conversation stays untouched as the backup.
    The fork gets a new name with timestamp to identify when compaction occurred.

    Strips any existing -compacted-YYYYMMDD-HHMMSS suffixes to prevent accumulation
    on repeated compactions.

    Examples:
    - "my-conversation" -> "my-conversation-compacted-20251029-073045"
    - "my-conversation-compacted-20251029-073045" -> "my-conversation-compacted-20251029-080000"

    Args:
        conversation_name: The current conversation directory name

    Returns:
        The compacted conversation name with timestamp

    Raises:
        ValueError: If conversation_name is empty
    """
    if not conversation_name:
        raise ValueError("conversation name cannot be empty")

    # Strip any existing compacted suffixes: -compacted-YYYYMMDDHHMM
    # This handles repeated compactions by removing previous timestamps
    base_name = conversation_name
    while True:
        # Match -compacted-{8 digits}-{6 digits} pattern
        new_name = re.sub(r"-compacted-\d{8}-\d{6}$", "", base_name)
        if new_name == base_name:  # No more changes
            break
        base_name = new_name

    if not base_name:  # Safety: if entire name was the suffix (shouldn't happen)
        base_name = conversation_name

    timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{base_name}-compacted-{timestamp}"


def autocompact_hook(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that checks if auto-compacting is needed and applies it.

    Runs after each message is processed to check if the conversation
    has grown too large with massive tool results.

    If compacting is needed:
    1. Creates compacted fork (original stays as backup)
    2. Manager switches to fork automatically
    3. Applies auto-compacting to fork conversation
    4. User continues in compacted conversation

    Args:
        manager: Conversation manager with log and workspace
    """

    global _last_autocompact_time

    # Check if enough time has passed since last autocompact attempt
    current_time = time.time()
    if current_time - _last_autocompact_time < _autocompact_min_interval:
        logger.debug(
            f"Skipping autocompact: {current_time - _last_autocompact_time:.1f}s "
            f"since last attempt (min interval: {_autocompact_min_interval}s)"
        )
        return

    messages = manager.log.messages

    action = should_auto_compact(messages)
    if action == "none":
        return

    _last_autocompact_time = current_time

    if action == "rule_based":
        logger.info("Auto-compacting triggered: conversation has massive tool results")

        # Apply auto-compacting to get compacted messages
        try:
            compacted_msgs = list(auto_compact_log(messages, logdir=manager.logdir))

            # Calculate reduction stats
            m = get_default_model()
            original_count = len(messages)
            compacted_count = len(compacted_msgs)
            original_tokens = len_tokens(messages, m.model) if m else 0
            compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

            # Create a view branch with compacted content
            # Master branch (main) stays intact with full history
            view_name = manager.get_next_view_name()
            manager.create_view(view_name, compacted_msgs)
            manager.switch_view(view_name)

            # Trigger CACHE_INVALIDATED hook - perfect time for plugins to update state
            # (e.g., attention-router can batch-apply decay and re-evaluate tiers)
            yield from trigger_hook(
                HookType.CACHE_INVALIDATED,
                manager=manager,
                reason="compact",
                tokens_before=original_tokens,
                tokens_after=compacted_tokens,
            )

            reduction_pct = (
                ((original_tokens - compacted_tokens) / original_tokens * 100)
                if original_tokens > 0
                else 0.0
            )
            # Yield a message indicating what happened
            yield Message(
                "system",
                f"🔄 Auto-compacted conversation to view branch:\n"
                f"• Messages: {original_count} → {compacted_count}\n"
                f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
                f"({reduction_pct:.1f}% reduction)\n"
                f"• View: {view_name} (master branch preserved with full history)",
                hide=True,  # Hide to prevent triggering responses
            )
        except Exception as e:
            logger.error(f"Auto-compact failed during compaction: {e}")
            # Don't yield error message to avoid triggering more hooks
            return

    elif action == "summarize":
        logger.info("Auto-summarize triggered: rule-based compaction insufficient")
        try:
            m = get_default_model()
            original_tokens = len_tokens(messages, m.model) if m else 0

            yield from _resume_via_llm(manager, messages, use_view_branch=True)

            compacted_tokens = len_tokens(manager.log.messages, m.model) if m else 0

            # Trigger CACHE_INVALIDATED hook — resume is even more aggressive
            # than rule-based compaction, so plugins need to know
            yield from trigger_hook(
                HookType.CACHE_INVALIDATED,
                manager=manager,
                reason="compact",
                tokens_before=original_tokens,
                tokens_after=compacted_tokens,
            )
        except Exception as e:
            logger.error(f"Auto-summarize failed: {e}")
            return


# Tool specification

tool = ToolSpec(
    name="autocompact",
    desc="Automatically compact conversations with massive tool results",
    instructions="",  # No user-facing instructions, runs automatically
    hooks={
        "autocompact": (
            HookType.TURN_POST,
            autocompact_hook,
            100,
        ),  # Low priority, runs after other hooks
    },
    commands={
        "compact": cmd_compact_handler,
    },
)
