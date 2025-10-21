"""
Time awareness tool.

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
from datetime import datetime
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation
from ..logmanager import Log
from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)

# Track conversation start times per workspace
_conversation_start_times: dict[str, datetime] = {}

# Track which time milestones have been shown per workspace
_shown_milestones: dict[str, set[int]] = {}


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

        # Initialize conversation start time if first message
        if workspace_str not in _conversation_start_times:
            _conversation_start_times[workspace_str] = datetime.now()
            _shown_milestones[workspace_str] = set()
            return

        # Calculate elapsed time in minutes
        elapsed = datetime.now() - _conversation_start_times[workspace_str]
        elapsed_minutes = int(elapsed.total_seconds() / 60)

        # Determine which milestone to show
        milestone = _get_next_milestone(elapsed_minutes)

        # Check if we should show this milestone
        if milestone and milestone not in _shown_milestones[workspace_str]:
            _shown_milestones[workspace_str].add(milestone)

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
                hide=True,
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


# Tool specification
tool = ToolSpec(
    name="time-awareness",
    desc="Time tracking awareness for conversations",
    instructions="""
This tool provides time awareness to help manage long-running conversations.

The assistant receives periodic updates about how much time has elapsed:
<system_info>Time elapsed: Xmin</system_info>

Time messages are shown at: 1min, 5min, 10min, 15min, 20min, then every 10 minutes.
""".strip(),
    available=True,
    hooks={
        "time_message": (
            HookType.TOOL_POST_EXECUTE.value,
            add_time_message,
            0,  # Normal priority
        ),
    },
)

__all__ = ["tool"]
