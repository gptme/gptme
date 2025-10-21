"""
Token budget awareness tool.

Implements context/token budget awareness similar to Claude 4.5's built-in feature,
but works across all LLM providers and tool formats.

Adds:
- <budget:token_budget>XXX</budget:token_budget> at session start
- <system_warning>Token usage: X/Y; Z remaining</system_warning> after message processing
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation
from ..logmanager import Log
from ..message import Message, len_tokens
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Cache for incremental token counting (avoids O(N²) behavior)
_token_totals: dict[str, int] = {}
_message_counts: dict[str, int] = {}


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
            hide=True,
        )

        logger.debug(f"Added token budget: {budget}")

    except Exception as e:
        logger.exception(f"Error adding token budget: {e}")


def add_token_usage_warning(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Add token usage warning after tool execution.

    Uses incremental token counting to avoid O(N²) behavior.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed (unused)

    Yields:
        System message with token usage warning
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
            # Incremental counting (O(1) amortized per message)
            current_count = len(log.messages)
            previous_count = _message_counts.get(log_id, 0)

            if previous_count == 0:
                # First time: count all messages
                used = len_tokens(log.messages, model.model)
                _token_totals[log_id] = used
                _message_counts[log_id] = current_count
            else:
                # Subsequent times: only count new messages
                new_messages = log.messages[previous_count:]
                if new_messages:
                    new_tokens = len_tokens(new_messages, model.model)
                    used = _token_totals.get(log_id, 0) + new_tokens
                    _token_totals[log_id] = used
                    _message_counts[log_id] = current_count
                else:
                    # No new messages (shouldn't happen but handle gracefully)
                    used = _token_totals.get(log_id, 0)

        remaining = budget - used

        # Add usage warning as a system message
        # Using hide=True so it doesn't show in terminal but is sent to the model
        yield Message(
            "system",
            f"<system_warning>Token usage: {used}/{budget}; {remaining} remaining</system_warning>",
            hide=True,
        )

        logger.debug(
            f"Token usage: {used}/{budget}; {remaining} remaining (incremental)"
        )

    except Exception as e:
        logger.exception(f"Error adding token usage warning: {e}")


# Tool specification
tool = ToolSpec(
    name="token-awareness",
    desc="Token budget awareness for conversations",
    instructions="""
This tool provides token budget awareness to the assistant across all LLM providers.

At the start of each conversation, the assistant receives information about the total token budget:
<budget:token_budget>XXX</budget:token_budget>

After each message is processed, the assistant receives an update on token usage:
<system_warning>Token usage: X/Y; Z remaining</system_warning>

This helps the assistant:
- Understand how much context capacity remains
- Plan responses to fit within the budget
- Manage long-running conversations effectively
""".strip(),
    available=True,
    hooks={
        "token_budget": (
            HookType.SESSION_START.value,
            add_token_budget,
            10,  # High priority to run early
        ),
        "token_usage": (
            HookType.TOOL_POST_EXECUTE.value,
            add_token_usage_warning,
            0,  # Normal priority
        ),
    },
)

__all__ = ["tool"]
