"""Subagent hook system — completion and progress notifications.

Handles the "fire-and-forget-then-get-alerted" pattern where subagent
completions and intermediate progress updates are delivered via the
LOOP_CONTINUE hook as system messages.
"""

import logging
import queue
from collections.abc import Generator
from typing import TYPE_CHECKING

from ...message import Message
from .types import Status, _completion_queue, _progress_queue

if TYPE_CHECKING:
    from ...logmanager import LogManager  # fmt: skip

logger = logging.getLogger(__name__)


def _get_complete_instruction(
    target: str = "orchestrator",
    *,
    supports_progress: bool = True,
    output_schema: type | None = None,
) -> str:
    """Get the standard instruction for using the complete tool.

    Used by both thread and subprocess modes to ensure consistent behavior.
    The instruction is intentionally minimal - profile system prompts and
    task context should guide what the complete answer should contain.

    Args:
        target: Who will review the result ("orchestrator", "parent", "planner")
        supports_progress: Whether to include the progress block instructions
        output_schema: Optional Pydantic model class. When set, the complete
            block must contain valid JSON matching the model's schema. The
            instruction is extended with the expected schema.
    """
    if output_schema is not None:
        import json

        schema_str = json.dumps(
            output_schema.model_json_schema()
            if hasattr(output_schema, "model_json_schema")
            else {"type": "object"},
            indent=2,
        )
        complete_block_hint = f"Valid JSON matching this schema:\n{schema_str}"
    else:
        complete_block_hint = "Your complete answer here."

    instruction = (
        "When finished, use the `complete` tool with your full answer/result.\n"
        f"Include everything the {target} needs - they shouldn't need to read the full log.\n"
        "```complete\n"
        f"{complete_block_hint}\n"
        "```\n"
        f"If you cannot proceed without more information from the {target}, use the `clarify` block instead:\n"
        "```clarify\n"
        "Your specific question here.\n"
        "```"
    )
    if supports_progress:
        instruction += (
            "\n"
            f"To send an intermediate progress update to the {target} (without stopping), use the `progress` block:\n"
            "```progress\n"
            "Brief status update: what you have done so far and what remains.\n"
            "```"
        )
    if output_schema is not None:
        instruction += (
            "\n"
            "IMPORTANT: Your `complete` block MUST contain valid JSON matching the schema above. "
            "Do not include any text outside the JSON object."
        )
    return instruction


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


def notify_progress(agent_id: str, message: str) -> None:
    """Add a subagent progress update to the notification queue.

    Called by the progress tool when a subagent sends an intermediate update.
    The parent's LOOP_CONTINUE hook delivers it as a system message so the
    orchestrator can react without blocking on subagent_wait().

    Note: Only works for thread-mode subagents (same process). Subprocess-mode
    subagents cannot share the in-process queue.

    Args:
        agent_id: The subagent's identifier
        message: Progress update message
    """
    _progress_queue.put((agent_id, message))
    logger.debug(f"Queued progress notification for subagent '{agent_id}'")


def _subagent_completion_hook(
    manager: "LogManager",
    interactive: bool,
    prompt_queue: object,
    no_confirm: bool = False,
) -> Generator[Message, None, None]:
    """Check for completed subagents and yield notification messages.

    This hook is triggered during each chat loop iteration via LOOP_CONTINUE.
    It checks the completion queue and yields system messages for any
    finished subagents, allowing the orchestrator to react naturally.

    Also drains the progress queue and delivers intermediate updates as
    ⏳ system messages.
    """

    # Drain progress notifications first (in-flight updates before completions)
    progress_updates: list[tuple[str, str]] = []
    while True:
        try:
            agent_id, message = _progress_queue.get_nowait()
            progress_updates.append((agent_id, message))
        except queue.Empty:
            break

    for agent_id, message in progress_updates:
        msg = f"⏳ Subagent '{agent_id}' progress: {message}"
        logger.debug(f"Delivering subagent progress notification: {msg}")
        yield Message("system", msg)

    # Drain completion notifications
    notifications: list[tuple[str, Status, str]] = []
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
        elif status == "clarification_needed":
            msg = (
                f"❓ Subagent '{agent_id}' needs clarification: {summary}\n"
                f"Call subagent_reply('{agent_id}', '<your answer>') to continue."
            )
        elif status == "timeout":
            msg = f"⏱️ Subagent '{agent_id}' timed out: {summary}"
        else:
            msg = f"❌ Subagent '{agent_id}' failed: {summary}"

        logger.debug(f"Delivering subagent notification: {msg}")
        yield Message("system", msg)
