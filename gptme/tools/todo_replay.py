"""
Todo replay hook that automatically replays todowrite operations at session start.

This ensures todo state is restored when resuming a conversation.
"""

import logging
from collections.abc import Generator

from ..hooks import HookType
from ..logmanager import Log
from ..message import Message
from .base import ToolSpec, ToolUse

logger = logging.getLogger(__name__)


def replay_todowrite_on_session_start(
    logdir, workspace, initial_msgs, **kwargs
) -> Generator[Message, None, None]:
    """Hook function that replays todowrite operations at session start.

    Args:
        logdir: Log directory path
        workspace: Workspace directory path
        initial_msgs: Initial messages in the log

    Yields:
        Messages about replay status (hidden)
    """
    if not initial_msgs:
        return

    # Check if there are any todowrite operations in the log
    has_todowrite = any(
        tooluse.tool == "todowrite"
        for msg in initial_msgs
        for tooluse in ToolUse.iter_from_content(msg.content)
    )

    if not has_todowrite:
        return

    logger.info("Detected todowrite operations, replaying to restore state...")

    try:
        # Import here to avoid circular dependency
        from ..commands import _replay_tool

        # Create a minimal Log object for replay
        log = Log(initial_msgs)

        # Replay todowrite operations
        _replay_tool(log, "todowrite")

        yield Message("system", "Restored todo state from previous session", hide=True)

    except Exception as e:
        logger.exception(f"Error replaying todowrite operations: {e}")
        yield Message(
            "system", f"Warning: Failed to restore todo state: {e}", hide=True
        )


# Tool specification
tool = ToolSpec(
    name="todo_replay",
    desc="Automatically replay todo operations at session start",
    instructions="""
This tool ensures todo state is preserved across sessions by replaying
todowrite operations when resuming a conversation.

It hooks into the SESSION_START event and automatically restores the
todo list state without requiring manual intervention.
""".strip(),
    available=True,
    hooks={
        "replay_todos": (
            HookType.SESSION_START.value,
            replay_todowrite_on_session_start,
            10,  # High priority: run early in session start
        )
    },
)

__all__ = ["tool"]
