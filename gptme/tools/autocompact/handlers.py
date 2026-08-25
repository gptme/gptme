"""Command handlers for the /compact command."""

import logging
from collections.abc import Generator

from ...llm.models import get_default_model
from ...logmanager import Log
from ...message import Message, len_tokens
from .config import _get_keep_head
from .decision import should_auto_compact
from .engine import auto_compact_log
from .resume import _resume_via_llm

logger = logging.getLogger(__name__)

# Mapping of deprecated mode names to their replacements
_DEPRECATED_MODES: dict[str, str] = {
    "auto": "trim",
    "resume": "summarize",
}

# All valid mode names (canonical + deprecated aliases)
_VALID_MODES = {"trim", "summarize"} | set(_DEPRECATED_MODES)


def cmd_compact_handler(ctx) -> Generator[Message, None, None]:
    """Command handler for /compact - compact the conversation using rule-based trimming or LLM-powered summarization."""

    ctx.manager.undo(1, quiet=True)

    # Parse arguments
    method = ctx.args[0] if ctx.args else "trim"

    if method not in _VALID_MODES:
        yield Message(
            "system",
            "Invalid compact method. Use 'trim' for rule-based compaction or 'summarize' for LLM-powered summarization.\n"
            "Usage: /compact [trim|summarize]",
        )
        return

    msgs = ctx.manager.log.messages[:-1]  # Exclude the /compact command itself

    # Handle deprecated aliases with a warning
    if method in _DEPRECATED_MODES:
        canonical = _DEPRECATED_MODES[method]
        logger.warning(
            f"/compact {method!r} is deprecated; use /compact {canonical!r} instead"
        )
        yield Message(
            "system",
            f"⚠️  '/compact {method}' is deprecated. Use '/compact {canonical}' instead.",
        )
        method = canonical

    if method == "trim":
        yield from _compact_trim(ctx, msgs)
    elif method == "summarize":
        yield from _compact_summarize(ctx, msgs)


def _compact_trim(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """Rule-based compaction: strips reasoning, truncates massive tool results, compresses old assistant messages."""

    decision = should_auto_compact(msgs)
    if decision != "rule_based":
        if decision == "summarize":
            yield Message(
                "system",
                "Rule-based trimming is unlikely to free enough context (savings too low). "
                "Consider using '/compact summarize' for LLM-powered summarization instead.",
            )
        else:
            yield Message(
                "system",
                "Trim compaction not needed. Conversation doesn't contain massive tool results or isn't close to context limits.",
            )
        return

    # Apply auto-compacting
    compacted_msgs = list(
        auto_compact_log(msgs, logdir=ctx.manager.logdir, keep_head=_get_keep_head())
    )

    # Calculate reduction stats
    original_count = len(msgs)
    compacted_count = len(compacted_msgs)
    m = get_default_model()
    original_tokens = len_tokens(msgs, m.model) if m else 0
    compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

    # Replace the conversation history
    ctx.manager.log = Log(compacted_msgs)
    ctx.manager.write()

    reduction_pct = (
        ((original_tokens - compacted_tokens) / original_tokens * 100)
        if original_tokens > 0
        else 0.0
    )
    yield Message(
        "system",
        f"✅ Trim compaction completed:\n"
        f"• Messages: {original_count} → {compacted_count}\n"
        f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
        f"({reduction_pct:.1f}% reduction)",
    )


# Keep the old name as an alias for backward compatibility with internal callers
_compact_auto = _compact_trim


def _compact_summarize(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """LLM-powered summarization: creates RESUME.md, extracts key files, and starts a new conversation with the context."""

    try:
        yield from _resume_via_llm(ctx.manager, msgs, use_view_branch=False)
    except Exception as e:
        # Include exception type for better debugging when message is empty
        error_msg = str(e).strip() or f"({type(e).__name__})"
        yield Message("system", f"❌ Failed to generate resume: {error_msg}")


# Keep the old name as an alias for backward compatibility with internal callers
_compact_resume = _compact_summarize
