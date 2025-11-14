"""
Time awareness hook.

Provides time feedback during conversations to help the assistant manage
long-running sessions effectively.

This helps the assistant:
- Understand conversation duration
- Plan work within time constraints
- Manage long-running autonomous sessions effectively
- Avoid timeouts and performance issues

Shows time elapsed messages at: 1min, 5min, 10min, 15min, 20min, then every 10min.
"""

import logging
from collections.abc import Generator
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message

logger = logging.getLogger(__name__)

# Context-local storage for time tracking (ensures context safety in gptme-server)
_conversation_start_times_var: ContextVar[dict[str, datetime] | None] = ContextVar(
    "conversation_start_times", default=None
)
_shown_milestones_var: ContextVar[dict[str, set[int]] | None] = ContextVar(
    "shown_milestones", default=None
)


def _ensure_locals():
    """Initialize context-local storage if needed."""
    if _conversation_start_times_var.get() is None:
        _conversation_start_times_var.set({})
    if _shown_milestones_var.get() is None:
        _shown_milestones_var.set({})


def add_time_message(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Add time elapsed message after tool execution.

    Shows messages at: 1min, 5min, 10min, 15min, 20min, then every 10min.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        tool_use: The tool being executed (unused)
    """
    try:
        if workspace is None:
            return

        workspace_str = str(workspace)

        # Ensure context-local storage is initialized
        _ensure_locals()

        conversation_start_times = _conversation_start_times_var.get()
        shown_milestones = _shown_milestones_var.get()
        assert conversation_start_times is not None
        assert shown_milestones is not None

        # Initialize conversation start time if first message
        if workspace_str not in conversation_start_times:
            conversation_start_times[workspace_str] = datetime.now()
            _conversation_start_times_var.set(conversation_start_times)
            shown_milestones[workspace_str] = set()
            _shown_milestones_var.set(shown_milestones)
            return

        # Calculate elapsed time in minutes
        elapsed = datetime.now() - conversation_start_times[workspace_str]
        elapsed_minutes = int(elapsed.total_seconds() / 60)

        # Determine which milestone to show
        milestone = _get_next_milestone(elapsed_minutes)

        # Check if we should show this milestone
        if milestone and milestone not in shown_milestones[workspace_str]:
            shown_milestones[workspace_str].add(milestone)
            _shown_milestones_var.set(shown_milestones)

            # Format time message
            hours = elapsed_minutes // 60
            minutes = elapsed_minutes % 60

            time_str = datetime.now().strftime("%H:%M")
            if hours > 0:
                elapsed_str = f"{hours}h {minutes}min" if minutes > 0 else f"{hours}h"
            else:
                elapsed_str = f"{minutes}min"

            message = Message(
                "system",
                f"<system_info>The time is now {time_str}. Time elapsed: {elapsed_str}</system_info>",
                # hide=True,
            )
            yield message

    except Exception as e:
        logger.exception(f"Error adding time message: {e}")


def _get_next_milestone(elapsed_minutes: int) -> int | None:
    """Get the next milestone to show based on elapsed minutes.

    Milestones: 1, 5, 10, 15, 20, then every 10 minutes.
    """
    if elapsed_minutes < 1:
        return None
    elif elapsed_minutes < 5:
        return 1
    elif elapsed_minutes < 10:
        return 5
    elif elapsed_minutes < 15:
        return 10
    elif elapsed_minutes < 20:
        return 15
    elif elapsed_minutes < 30:
        return 20
    else:
        # Every 10 minutes after 20
        return (elapsed_minutes // 10) * 10


def register() -> None:
    """Register the time awareness hook with the hook system."""
    register_hook(
        "time_awareness.time_message",
        HookType.TOOL_POST_EXECUTE,
        add_time_message,
        priority=0,  # Normal priority
    )
    logger.debug("Registered time awareness hook")
