"""
Auto-compacting tool for handling conversations with massive tool results.

Automatically triggers when conversation has massive tool results that would
prevent resumption, compacting them to allow the conversation to continue.
"""

import logging
from pathlib import Path

from ..message import Message
from ..util.auto_compact import auto_compact_log, should_auto_compact

logger = logging.getLogger(__name__)


def autocompact_hook(log: list[Message], workspace: Path | None, manager=None):
    """
    Hook that checks if auto-compacting is needed and applies it.

    Runs after each message is processed to check if the conversation
    has grown too large with massive tool results.

    If compacting is needed:
    1. Forks the conversation to preserve original state
    2. Applies auto-compacting to current conversation
    3. Persists the compacted log
    """

    from ..llm.models import get_default_model
    from ..logmanager import Log
    from ..message import len_tokens

    if not should_auto_compact(log):
        return

    if manager is None:
        logger.warning("Auto-compact hook called without manager, cannot persist")
        return

    logger.info("Auto-compacting triggered: conversation has massive tool results")

    # Fork conversation to preserve original state
    fork_name = f"{manager.logfile.parent.name}-before-compact"
    try:
        manager.fork(fork_name)
        logger.info(f"Forked conversation to '{fork_name}' before compacting")
    except Exception as e:
        logger.error(f"Failed to fork conversation: {e}")
        yield Message(
            "system",
            f"⚠️ Auto-compact: Failed to fork conversation: {e}\n"
            "Skipping auto-compact to preserve safety.",
            hide=False,
        )
        return

    # Apply auto-compacting
    compacted_msgs = list(auto_compact_log(log))

    # Calculate reduction stats
    m = get_default_model()
    original_count = len(log)
    compacted_count = len(compacted_msgs)
    original_tokens = len_tokens(log, m.model) if m else 0
    compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

    # Replace the log with compacted version
    manager.log = Log(compacted_msgs)
    manager.write()

    # Yield a message indicating what happened
    yield Message(
        "system",
        f"🔄 Auto-compacted conversation due to massive tool results:\n"
        f"• Messages: {original_count} → {compacted_count}\n"
        f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
        f"({((original_tokens - compacted_tokens) / original_tokens * 100):.1f}% reduction)\n"
        f"Original state preserved in '{fork_name}'.",
        hide=False,
    )


# Tool specification
from .base import ToolSpec
from ..hooks import HookType

tool = ToolSpec(
    name="autocompact",
    desc="Automatically compact conversations with massive tool results",
    instructions="",  # No user-facing instructions, runs automatically
    hooks={
        "autocompact": (
            HookType.MESSAGE_POST_PROCESS,
            autocompact_hook,
            100,
        ),  # Low priority, runs after other hooks
    },
)
__doc__ = tool.desc
