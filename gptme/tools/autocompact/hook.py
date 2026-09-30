"""Hook implementation and tool registration for auto-compacting.

Provides the auto-trigger hook that runs after each message and
the ToolSpec that registers the tool with the framework.
"""

import logging
import re
import time
from collections.abc import Generator
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ...hooks import HookType, StopPropagation, trigger_hook
from ...llm.models import get_default_model
from ...message import Message, len_tokens
from ...util.context_budget import get_context_budget
from ..base import ToolSpec, ToolUse
from .config import _get_keep_head
from .context_provider import CompressionConfig, get_context_provider
from .decision import MIN_SAVINGS_RATIO, TRIM_TARGET_RATIO, should_auto_compact
from .events import append_compaction_event
from .handlers import cmd_compact_handler
from .resume import _resume_via_llm

if TYPE_CHECKING:
    from ...logmanager import LogManager

logger = logging.getLogger(__name__)

# Reentrancy guard to prevent infinite loops. Keyed by (conversation logdir,
# branch) so concurrent server sessions — and sibling branches of the same
# conversation — do not share a process-global cooldown. The value is
# (timestamp, message_count): a later attempt on the same conversation is
# allowed inside the interval when the log has grown (e.g. tool results).
_last_autocompact_attempt: dict[tuple[str, str], tuple[float, int]] = {}
_autocompact_min_interval = 60  # Minimum 60 seconds between unchanged attempts

# Bound the cooldown map so a long-lived server does not accumulate one entry
# per conversation it has ever compacted.
_MAX_TRACKED_CONVERSATIONS = 512

# Failure latch: after a summarize attempt fails or is rejected, do not retry
# the summarize path until either a summarize succeeds or the conversation has
# grown by this many provider-visible messages. Without it every tool step
# re-ran a full-window summarize, because the (previously provider-visible)
# progress message grew the message count past the throttle. Keys are
# (logdir, branch); values are the effective message count at the failure.
_failed_summarize: dict[tuple[str, str], int] = {}
_FAILURE_RETRY_GROWTH_MESSAGES = 20


def _effective_message_count(messages: list[Message]) -> int:
    """Count provider-visible messages (excludes UI-only status/hook messages)."""
    return sum(1 for m in messages if not m.ui_only)


def _has_pending_tool_calls(messages: list[Message]) -> bool:
    """True if the last assistant message requests tools that have not run yet.

    Compacting here would send ``assistant(tool_use X)`` as the final message,
    or build a view that drops X and orphans the later tool result. Server
    TURN_POST fires before tools execute, so this guard is what keeps the
    pre-tool call site from compacting.
    """
    last_assistant_idx = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].role == "assistant"
        ),
        None,
    )
    if last_assistant_idx is None:
        return False
    calls = [
        tu
        for tu in ToolUse.iter_from_content(messages[last_assistant_idx].content)
        if tu.is_runnable
    ]
    if not calls:
        return False
    # Only system messages can be tool results. UI-only status/progress
    # notices are not results; neither is a user (or assistant) message that
    # lands between the call and its result. Counting those would let the
    # markdown fallback treat an unanswered call as done.
    results = [
        m
        for m in messages[last_assistant_idx + 1 :]
        if not m.ui_only and m.role == "system"
    ]
    if not results:
        return True
    # Prefer explicit call-id matching when the tool format carries ids.
    call_ids = {tu.call_id for tu in calls if tu.call_id}
    if call_ids:
        result_ids = {m.call_id for m in results if m.call_id}
        return not call_ids.issubset(result_ids)
    # Markdown format has no ids; one result message per call is expected, so a
    # partial result set (one of several tools still pending confirmation) is
    # still pending rather than treated as fully answered.
    return len(results) < len(calls)


def _prune_attempts(now: float) -> None:
    """Drop cooldown/latch entries for conversations we no longer need to track."""
    # The failure latch is bounded independently: its keys need not overlap the
    # cooldown map, so it must not be gated behind the cooldown map's size.
    if len(_failed_summarize) > _MAX_TRACKED_CONVERSATIONS:
        for key in list(_failed_summarize)[
            : len(_failed_summarize) - _MAX_TRACKED_CONVERSATIONS
        ]:
            del _failed_summarize[key]
    if len(_last_autocompact_attempt) <= _MAX_TRACKED_CONVERSATIONS:
        return
    # Prefer entries past the cooldown window; if that is not enough (a burst
    # of fresh conversations), drop oldest-first so the map stays bounded.
    cutoff = now - _autocompact_min_interval
    for key, (last_time, _) in list(_last_autocompact_attempt.items()):
        if last_time < cutoff:
            del _last_autocompact_attempt[key]
    overflow = len(_last_autocompact_attempt) - _MAX_TRACKED_CONVERSATIONS
    if overflow > 0:
        for key, _ in sorted(
            _last_autocompact_attempt.items(), key=lambda item: item[1][0]
        )[:overflow]:
            del _last_autocompact_attempt[key]


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
    *,
    llm_unlocked: AbstractContextManager[object] | None = None,
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

    current_time = time.time()
    _prune_attempts(current_time)
    conv_key = (str(manager.logdir), manager.current_branch)
    messages = manager.log.messages
    # Growth is measured in provider-visible messages: hook-yielded status
    # messages must not defeat the throttle (they no longer reach the provider).
    n_messages = _effective_message_count(messages)
    last_attempt = _last_autocompact_attempt.get(conv_key)
    if last_attempt is not None:
        last_time, last_len = last_attempt
        if (
            current_time - last_time < _autocompact_min_interval
            and n_messages == last_len
        ):
            logger.debug(
                f"Skipping autocompact: {current_time - last_time:.1f}s "
                f"since last attempt on {conv_key} "
                f"(min interval: {_autocompact_min_interval}s, log unchanged)"
            )
            return

    model = get_default_model()
    budget = (
        get_context_budget(
            model.context,
            max_output=model.max_output or 8192,
            model_id=model.full,
            model_context_budget=model.context_budget,
        )
        if model is not None
        else None
    )

    action = should_auto_compact(messages, limit=budget, keep_head=_get_keep_head())
    if action == "none":
        return

    # Never compact while the last assistant message has unanswered tool calls:
    # the summarize request would end on a tool_use and strict providers reject
    # it, and a view built here would orphan the pending tool result.
    if _has_pending_tool_calls(messages):
        logger.debug(
            "Skipping autocompact: last assistant message has pending tool calls"
        )
        return

    # Failure latch: stay on the trim path until the conversation grows enough
    # (or a summarize succeeds) before retrying the failing summarize.
    failed_at = _failed_summarize.get(conv_key)
    if failed_at is not None:
        if n_messages < failed_at:
            # The conversation shrank below the failure baseline (e.g. a manual
            # /compact or a view switch replaced the log). Rebase the latch to
            # the new count, otherwise n_messages - failed_at stays negative
            # forever and the latch can never release.
            _failed_summarize[conv_key] = failed_at = n_messages
        if n_messages - failed_at < _FAILURE_RETRY_GROWTH_MESSAGES:
            if action == "summarize":
                # Trim-only: a summarize just failed, so fall back to the cheap
                # rule-based trim instead of leaving the conversation over budget
                # until the next growth step.
                logger.info(
                    "Previous summarize failed; using trim-only until %d more "
                    "messages or a successful compaction",
                    _FAILURE_RETRY_GROWTH_MESSAGES,
                )
                action = "rule_based"
        else:
            _failed_summarize.pop(conv_key, None)

    if action == "rule_based":
        logger.info("Auto-compacting triggered: conversation has massive tool results")

        # Apply auto-compacting to get compacted messages
        try:
            provider = get_context_provider("default")
            config = CompressionConfig(
                limit=budget,
                logdir=manager.logdir,
                keep_head=_get_keep_head(),
                trim_target_ratio=TRIM_TARGET_RATIO,
            )
            compacted_msgs = provider.compress(messages, config).messages

            # Calculate reduction stats
            m = get_default_model()
            original_count = len(messages)
            compacted_count = len(compacted_msgs)
            original_tokens = len_tokens(messages, m.model) if m else 0
            compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

            # Post-hoc savings gate: the pre-trigger estimate is approximate, so
            # verify the view we are about to install actually saves enough to
            # justify the cache invalidation. Rejecting a near-useless view keeps
            # the promise "no compaction below MIN_SAVINGS_RATIO". Mark the
            # attempt so the unchanged log does not re-fire every step; the next
            # growth window can retry (by then the estimate may take the
            # summarize path instead).
            reduction_ratio = (
                (original_tokens - compacted_tokens) / original_tokens
                if original_tokens > 0
                else 0.0
            )
            # Fail open when token measurement is unavailable (no default
            # model): we cannot judge the savings, so do not block a trim the
            # estimate already accepted.
            if m is not None and reduction_ratio < MIN_SAVINGS_RATIO:
                logger.warning(
                    "Rejecting rule-based auto-compact view: actual savings "
                    f"{reduction_ratio:.1%} below threshold {MIN_SAVINGS_RATIO:.0%}"
                )
                _last_autocompact_attempt[conv_key] = (current_time, n_messages)
                # The estimate said rule-based was worth it, but the real
                # view saves too little. Fall back to the LLM summarizer (the
                # path chosen when the estimate itself shows low rule-based
                # savings) instead of leaving the over-budget log uncompacted
                # until a provider overflow. Skip while a recent summarize
                # failure is latched — retrying it every step would thrash.
                if conv_key not in _failed_summarize:
                    action = "summarize"

            else:
                # Create a view branch with compacted content
                # Master branch (main) stays intact with full history
                view_name = manager.get_next_view_name()
                manager.create_view(view_name, compacted_msgs)
                manager.switch_view(view_name)
                post_trim_count = _effective_message_count(manager.log.messages)
                _last_autocompact_attempt[conv_key] = (current_time, post_trim_count)
                # The latch deliberately survives a successful trim: an ineffective
                # trim that leaves the conversation over budget must not reset the
                # growth clock, or summarize retries (and fails) every 20 messages.
                # The baseline may only move *down* — to the post-trim count when a
                # trim actually shrinks the view (otherwise growth measured from a
                # larger pre-trim count could never reach the threshold). A trim that
                # preserves the message count leaves the baseline untouched, so
                # growth keeps accumulating across repeated trims.
                if conv_key in _failed_summarize:
                    _failed_summarize[conv_key] = min(
                        _failed_summarize[conv_key], post_trim_count
                    )

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
                append_compaction_event(
                    manager.logdir,
                    trigger="budget",
                    method="trim",
                    tokens_before=original_tokens,
                    tokens_after=compacted_tokens,
                    messages_before=original_count,
                    messages_after=compacted_count,
                    elapsed_seconds=time.time() - current_time,
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
                    ui_only=True,  # Status message: never sent to the provider
                )
        except Exception as e:
            logger.error(f"Auto-compact failed during compaction: {e}")
            # Don't yield error message to avoid triggering more hooks
            return

    if action == "summarize":
        logger.info("Auto-summarize triggered: rule-based compaction insufficient")
        m = get_default_model()
        original_tokens = len_tokens(messages, m.model) if m else 0
        original_count = len(messages)
        view_before = manager.current_view
        try:
            yield from _resume_via_llm(
                manager,
                messages,
                use_view_branch=True,
                llm_unlocked=llm_unlocked,
            )
        except Exception as e:
            logger.error(f"Auto-summarize failed: {e}")
            _failed_summarize[conv_key] = n_messages
            _prune_attempts(current_time)
            return

        if manager.current_view == view_before:
            # The summarizer yielded an error/status message without compacting
            # (no view branch was created). Latch to trim-only.
            logger.warning(
                "Auto-summarize produced no compaction; latching trim-only path"
            )
            _failed_summarize[conv_key] = n_messages
            _prune_attempts(current_time)
            return

        _last_autocompact_attempt[conv_key] = (
            current_time,
            _effective_message_count(manager.log.messages),
        )
        _failed_summarize.pop(conv_key, None)

        try:
            compacted_tokens = len_tokens(manager.log.messages, m.model) if m else 0
            append_compaction_event(
                manager.logdir,
                trigger="budget",
                method="summarize",
                tokens_before=original_tokens,
                tokens_after=compacted_tokens,
                messages_before=original_count,
                messages_after=len(manager.log.messages),
                elapsed_seconds=time.time() - current_time,
            )

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
