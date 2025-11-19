"""
Token budget awareness hook.

Implements context/token budget awareness similar to Claude 4.5's built-in feature,
but works across all LLM providers and tool formats.

Adds:
- <budget:token_budget>XXX</budget:token_budget> at session start
- <system_warning>Token usage: X/Y; Z remaining</system_warning> after message processing
"""

import logging
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message, len_tokens

logger = logging.getLogger(__name__)

# Context-local storage for token tracking (ensures context safety in gptme-server)
_token_totals_var: ContextVar[dict[str, int] | None] = ContextVar(
    "token_totals", default=None
)
_message_counts_var: ContextVar[dict[str, int] | None] = ContextVar(
    "message_counts", default=None
)
_last_warning_tokens_var: ContextVar[dict[str, int] | None] = ContextVar(
    "last_warning_tokens", default=None
)


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _token_totals_var.get() is None:
        _token_totals_var.set({})
    if _message_counts_var.get() is None:
        _message_counts_var.set({})
    if _last_warning_tokens_var.get() is None:
        _last_warning_tokens_var.set({})


# Thresholds for showing warnings
WARNING_INTERVAL = 10000  # Show warning every 10k tokens
WARNING_PERCENTAGES = [0.5, 0.75, 0.9, 0.95]  # Show at 50%, 75%, 90%, 95%


def add_token_budget(
    logdir: Path, workspace: Path | None, initial_msgs: list[Message]
) -> Generator[Message | StopPropagation, None, None]:
    """Add token budget tag at session start.

    Args:
        logdir: Log directory path
        workspace: Workspace directory path
        initial_msgs: Initial messages in the conversation

    Yields:
        System message with token budget tag
    """
    try:
        from ..llm.models import get_default_model

        model = get_default_model()
        if not model:
            logger.debug("No model loaded, skipping token budget")
            return

        budget = model.context

        # Add budget tag as a system message
        # Using hide=True so it doesn't show in terminal but is sent to the model
        yield Message(
            "system",
            f"<budget:token_budget>{budget}</budget:token_budget>",
            # hide=True,
        )

        logger.debug(f"Added token budget: {budget}")

    except Exception as e:
        logger.exception(f"Error adding token budget: {e}")


def add_token_usage_warning(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Add token usage warning after tool execution.

    Uses incremental token counting to avoid O(N²) behavior.
    Only shows warnings at meaningful thresholds to avoid noise.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed (unused)

    Yields:
        System message with token usage warning (only at thresholds)
    """
    try:
        from ..llm.models import get_default_model

        model = get_default_model()
        if not model:
            logger.debug("No model loaded, skipping token usage warning")
            return

        budget = model.context

        # Use workspace as unique identifier for the conversation
        # If workspace is None, fall back to recounting (less efficient but correct)
        log_id = str(workspace) if workspace else None

        # Calculate token usage
        if log_id is None:
            # No workspace identifier: fall back to counting all messages
            # This is less efficient (O(N) per call) but ensures correctness
            used = len_tokens(log.messages, model.model)
        else:
            # Ensure context-local storage is initialized
            _ensure_locals()

            token_totals = _token_totals_var.get()
            message_counts = _message_counts_var.get()
            last_warning_tokens = _last_warning_tokens_var.get()
            assert token_totals is not None
            assert message_counts is not None
            assert last_warning_tokens is not None

            # Incremental counting (O(1) amortized per message)
            current_count = len(log.messages)
            previous_count = message_counts.get(log_id, 0)

            if previous_count == 0:
                # First time: count all messages
                used = len_tokens(log.messages, model.model)
                token_totals[log_id] = used
                _token_totals_var.set(token_totals)
                message_counts[log_id] = current_count
                _message_counts_var.set(message_counts)
                last_warning_tokens[log_id] = 0
                _last_warning_tokens_var.set(last_warning_tokens)
            else:
                # Subsequent times: only count new messages
                new_messages = log.messages[previous_count:]
                if new_messages:
                    new_tokens = len_tokens(new_messages, model.model)
                    used = token_totals.get(log_id, 0) + new_tokens
                    token_totals[log_id] = used
                    _token_totals_var.set(token_totals)
                    message_counts[log_id] = current_count
                    _message_counts_var.set(message_counts)
                else:
                    # No new messages (shouldn't happen but handle gracefully)
                    used = token_totals.get(log_id, 0)

        # Check if we should show a warning
        should_warn = False
        warning_reason = ""

        if log_id:
            _ensure_locals()
            last_warning_tokens = _last_warning_tokens_var.get()
            assert last_warning_tokens is not None
            last_warning = last_warning_tokens.get(log_id, 0)

            # Check if we've crossed a percentage threshold
            current_percent = used / budget
            last_percent = last_warning / budget if last_warning > 0 else 0

            for threshold in WARNING_PERCENTAGES:
                if current_percent >= threshold and last_percent < threshold:
                    should_warn = True
                    warning_reason = f"crossed {int(threshold * 100)}% threshold"
                    break

            # Check if we've used another WARNING_INTERVAL tokens
            if not should_warn and (used - last_warning) >= WARNING_INTERVAL:
                should_warn = True
                warning_reason = f"used {WARNING_INTERVAL:,} more tokens"
        else:
            # No log_id: show warning every time (fallback behavior)
            should_warn = True
            warning_reason = "no workspace tracking"

        if should_warn:
            remaining = budget - used

            # Update last warning tracker
            if log_id:
                _ensure_locals()
                last_warning_tokens = _last_warning_tokens_var.get()
                assert last_warning_tokens is not None
                last_warning_tokens[log_id] = used
                _last_warning_tokens_var.set(last_warning_tokens)

            # Add usage warning as a system message
            # Using hide=True so it doesn't show in terminal but is sent to the model
            yield Message(
                "system",
                f"<system_warning>Token usage: {used}/{budget}; {remaining} remaining</system_warning>",
                # hide=True,
            )

            logger.debug(
                f"Token usage warning ({warning_reason}): {used}/{budget}; {remaining} remaining"
            )

    except Exception as e:
        logger.exception(f"Error adding token usage warning: {e}")


def register() -> None:
    """Register the token awareness hooks with the hook system."""
    register_hook(
        "token_awareness.token_budget",
        HookType.SESSION_START,
        add_token_budget,
        priority=10,  # High priority to run early
    )
    register_hook(
        "token_awareness.token_usage",
        HookType.TOOL_POST_EXECUTE,
        add_token_usage_warning,
        priority=0,  # Normal priority
    )
    logger.debug("Registered token awareness hooks")
