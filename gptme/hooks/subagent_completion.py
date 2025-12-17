"""Hook for delivering subagent completion notifications via LOOP_CONTINUE.

This hook implements the "fire-and-forget-then-get-alerted" pattern for async subagents.
Instead of requiring active polling with subagent_wait(), completions are delivered
as system messages during the normal chat loop.
"""

import logging
import queue
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)

# Thread-safe queue for completed subagents
# Each entry is (agent_id, status, summary)
_completion_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()


def notify_completion(agent_id: str, status: str, summary: str) -> None:
    """Add a subagent completion to the notification queue.

    Called by the subagent tool's monitor thread when a subagent finishes.

    Args:
        agent_id: The subagent's identifier
        status: "success" or "failure"
        summary: Brief summary of the result
    """
    _completion_queue.put((agent_id, status, summary))
    logger.debug(f"Queued completion notification for subagent '{agent_id}': {status}")


def subagent_completion_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: Any,
) -> Generator[Message | StopPropagation, None, None]:
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


def register() -> None:
    """Register the subagent completion hook."""
    register_hook(
        name="subagent_completion",
        hook_type=HookType.LOOP_CONTINUE,
        func=subagent_completion_hook,
        priority=50,  # High priority to ensure timely delivery
        enabled=True,
    )
    logger.debug("Registered subagent completion hook")
