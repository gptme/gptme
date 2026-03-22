"""Command handlers for the /compact command."""

from collections.abc import Generator

from ...llm.models import get_default_model
from ...logmanager import Log
from ...message import Message, len_tokens
from .decision import should_auto_compact
from .engine import auto_compact_log
from .resume import _resume_via_llm


def cmd_compact_handler(ctx) -> Generator[Message, None, None]:
    """Command handler for /compact - compact the conversation using auto-compacting or LLM-powered resume generation."""

    ctx.manager.undo(1, quiet=True)

    # Parse arguments
    method = ctx.args[0] if ctx.args else "auto"

    if method not in ["auto", "resume"]:
        yield Message(
            "system",
            "Invalid compact method. Use 'auto' for auto-compacting or 'resume' for LLM-powered resume generation.\n"
            "Usage: /compact [auto|resume]",
        )
        return

    msgs = ctx.manager.log.messages[:-1]  # Exclude the /compact command itself

    if method == "auto":
        yield from _compact_auto(ctx, msgs)
    elif method == "resume":
        yield from _compact_resume(ctx, msgs)


def _compact_auto(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """Auto-compact using the aggressive compacting algorithm."""

    if should_auto_compact(msgs) != "rule_based":
        yield Message(
            "system",
            "Auto-compacting not needed. Conversation doesn't contain massive tool results or isn't close to context limits.",
        )
        return

    # Apply auto-compacting
    compacted_msgs = list(auto_compact_log(msgs, logdir=ctx.manager.logdir))

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
        f"✅ Auto-compacting completed:\n"
        f"• Messages: {original_count} → {compacted_count}\n"
        f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
        f"({reduction_pct:.1f}% reduction)",
    )


def _compact_resume(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """LLM-powered compact that creates RESUME.md, extracts key files, and starts a new conversation with the context."""

    try:
        yield from _resume_via_llm(ctx.manager, msgs, use_view_branch=False)
    except Exception as e:
        # Include exception type for better debugging when message is empty
        error_msg = str(e).strip() or f"({type(e).__name__})"
        yield Message("system", f"❌ Failed to generate resume: {error_msg}")
