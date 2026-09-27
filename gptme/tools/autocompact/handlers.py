"""Command handlers for the /compact command."""

import logging
from collections.abc import Generator
from time import monotonic

from ...config import get_project_config
from ...llm.models import get_default_model
from ...logmanager import Log
from ...message import Message, len_tokens
from .config import _get_keep_head
from .context_provider import CompressionConfig, get_context_provider
from .decision import should_auto_compact
from .events import append_compaction_event
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
    """Command handler for /compact - compact the conversation using rule-based trimming or LLM-powered summarization.

    Usage:
        /compact [trim|summarize] [instructions...]

    The optional instructions are appended to the checkpoint prompt when using
    the summarize method (or the default). This is the per-invocation equivalent
    of the [context] compact_instructions project config key.
    """

    ctx.manager.undo(1, quiet=True)

    # Parse arguments
    # Usage: /compact [mode] [instructions...]
    #   mode: trim | summarize (or deprecated aliases auto / resume)
    #   instructions: optional text appended to the summarize checkpoint prompt
    args = ctx.args or []
    method = args[0] if args else "trim"
    extra_instructions = " ".join(args[1:]) if len(args) > 1 else None

    if method not in _VALID_MODES | set(_DEPRECATED_MODES):
        yield Message(
            "system",
            "Invalid compact method. Use 'trim' for rule-based compaction or 'summarize' for LLM-powered summarization.\n"
            "Usage: /compact [trim|summarize] [instructions...]",
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
        yield from _compact_summarize(
            ctx, msgs, compact_instructions=extra_instructions
        )


def _compact_trim(ctx, msgs: list[Message]) -> Generator[Message, None, None]:
    """Rule-based compaction: strips reasoning, truncates massive tool results, compresses old assistant messages."""

    decision = should_auto_compact(msgs, keep_head=_get_keep_head())
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

    # Apply auto-compacting using the provider interface
    started = monotonic()
    provider = get_context_provider("default")
    config = CompressionConfig(logdir=ctx.manager.logdir, keep_head=_get_keep_head())
    compacted_msgs = provider.compress(msgs, config).messages

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
    append_compaction_event(
        ctx.manager.logdir,
        trigger="manual",
        method="trim",
        tokens_before=original_tokens,
        tokens_after=compacted_tokens,
        messages_before=original_count,
        messages_after=compacted_count,
        elapsed_seconds=monotonic() - started,
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


def _compact_summarize(
    ctx,
    msgs: list[Message],
    compact_instructions: str | None = None,
) -> Generator[Message, None, None]:
    """LLM-powered summarization: creates RESUME.md, extracts key files, and starts a new conversation with the context."""

    # Read project-level compact settings so manual /compact honors gptme.toml config.
    proj_keep_recent = 20_000
    proj_instructions: str | None = None
    try:
        proj_cfg = get_project_config(ctx.manager.workspace)
        if proj_cfg and proj_cfg.context:
            if proj_cfg.context.keep_recent_tokens is not None:
                proj_keep_recent = proj_cfg.context.keep_recent_tokens
            proj_instructions = proj_cfg.context.compact_instructions
    except Exception:
        pass  # config read failure is non-fatal; fall back to defaults

    # Inline instructions (from /compact summarize <text>) append to project instructions.
    merged_instructions: str | None
    if compact_instructions and proj_instructions:
        merged_instructions = f"{proj_instructions}\n{compact_instructions}"
    else:
        merged_instructions = compact_instructions or proj_instructions

    started = monotonic()
    m = get_default_model()
    original_tokens = len_tokens(msgs, m.model) if m else 0
    try:
        yield from _resume_via_llm(
            ctx.manager,
            msgs,
            use_view_branch=False,
            compact_instructions=merged_instructions,
            keep_recent_tokens=proj_keep_recent,
        )
        compacted_messages = ctx.manager.log.messages
        if not isinstance(compacted_messages, list):
            return
        compacted_tokens = len_tokens(compacted_messages, m.model) if m else 0
        append_compaction_event(
            ctx.manager.logdir,
            trigger="manual",
            method="summarize",
            tokens_before=original_tokens,
            tokens_after=compacted_tokens,
            messages_before=len(msgs),
            messages_after=len(compacted_messages),
            elapsed_seconds=monotonic() - started,
        )
    except Exception as e:
        # Include exception type for better debugging when message is empty
        error_msg = str(e).strip() or f"({type(e).__name__})"
        yield Message("system", f"❌ Failed to generate resume: {error_msg}")


# Keep the old name as an alias for backward compatibility with internal callers
_compact_resume = _compact_summarize
