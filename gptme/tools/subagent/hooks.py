"""Subagent hook system — completion notifications and instructions.

Handles the "fire-and-forget-then-get-alerted" pattern where subagent
completions are delivered via the LOOP_CONTINUE hook as system messages.
"""

import logging
import queue
from collections.abc import Generator
from typing import TYPE_CHECKING

from ...message import Message
from .types import Status, _completion_queue

if TYPE_CHECKING:
    from ...logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)


def _get_complete_instruction(target: str = "orchestrator") -> str:
    """Get the standard instruction for using the complete tool.

    Used by both thread and subprocess modes to ensure consistent behavior.
    The instruction is intentionally minimal - profile system prompts and
    task context should guide what the complete answer should contain.
    """
    return (
        "When finished, use the `complete` tool with your full answer/result.\n"
        f"Include everything the {target} needs - they shouldn't need to read the full log.\n"
        "```complete\n"
        "Your complete answer here.\n"
        "```"
    )


def notify_completion(agent_id: str, status: Status, summary: str) -> None:
    """Add a subagent completion to the notification queue.

    Called by the monitor thread when a subagent finishes. The queued
    notification will be delivered via the subagent_completion hook
    during the next LOOP_CONTINUE cycle.

    Args:
        agent_id: The subagent's identifier
        status: "success" or "failure"
        summary: Brief summary of the result
    """
    _completion_queue.put((agent_id, status, summary))
    logger.debug(f"Queued completion notification for subagent '{agent_id}': {status}")


def _subagent_completion_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: object,
) -> Generator[Message, None, None]:
    """Check for completed subagents and yield notification messages.

    This hook is triggered during each chat loop iteration via LOOP_CONTINUE.
    It checks the completion queue and yields system messages for any
    finished subagents, allowing the orchestrator to react naturally.
    """

    notifications = []

    # Drain all available notifications
    while True:
        try:
            agent_id, status, summary = _completion_queue.get_nowait()
            notifications.append((agent_id, status, summary))
        except queue.Empty:
            break

    # Yield messages for each completion
    for agent_id, status, summary in notifications:
        if status == "success":
            msg = f"✅ Subagent '{agent_id}' completed: {summary}"
        else:
            msg = f"❌ Subagent '{agent_id}' failed: {summary}"

        logger.debug(f"Delivering subagent notification: {msg}")
        yield Message("system", msg)
