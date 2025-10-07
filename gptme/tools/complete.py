"""Complete tool - signals that the autonomous session is finished."""

import logging

from ..hooks import HookType
from ..message import Message
from .base import ConfirmFunc, ToolSpec, ToolUse

logger = logging.getLogger(__name__)


class SessionCompleteException(Exception):
    """Exception raised to signal that the session should end."""

    pass


def execute_complete(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Message:
    """Signal that the autonomous session is complete and ready to exit."""
    return Message(
        "system",
        "Task complete. Autonomous session finished.",
        quiet=False,
    )


def complete_hook(log: list[Message], workspace, manager=None):
    """
    Hook that detects complete tool call and prevents next generation.

    Runs at GENERATION_PRE (before generating response) to stop the session
    immediately after complete tool is called.
    """
    # Handle both Log objects and list[Message]
    messages = log.messages if hasattr(log, "messages") else log

    logger.debug(f"complete_hook: checking {len(messages) if messages else 0} messages")

    if not messages:
        logger.debug("complete_hook: no messages")
        return

    # Look for complete tool call in the last assistant message
    last_assistant_msg = next(
        (m for m in reversed(messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        logger.debug("complete_hook: no assistant messages")
        return

    logger.debug(
        "complete_hook: checking last assistant message for complete tool call"
    )

    # Check if the assistant called the complete tool
    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    for tool_use in tool_uses:
        if tool_use.tool == "complete":
            logger.info("Complete tool call detected, stopping session immediately")
            raise SessionCompleteException("Session completed via complete tool")

    logger.debug("complete_hook: complete tool not detected")


def auto_reply_hook(
    log: list[Message], workspace, manager=None, interactive=True, prompt_queue=None
):
    """
    Hook that implements auto-reply mechanism for autonomous operation.

    If in non-interactive mode and last assistant message had no tools,
    inject an auto-reply to ensure the assistant does work.

    This is called via LOOP_CONTINUE hook, which receives interactive and prompt_queue.
    """
    # Only run in non-interactive mode
    if interactive:
        return

    # Skip if there are queued prompts
    if prompt_queue:
        return

    last_assistant_msg = next((m for m in reversed(log) if m.role == "assistant"), None)
    if not last_assistant_msg:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    if tool_uses:
        return  # Has tools, no need to prompt

    # Check if we already auto-replied
    last_user_msg = next((m for m in reversed(log) if m.role == "user"), None)
    if last_user_msg and "use the `complete` tool" in last_user_msg.content:
        # Already auto-replied, assistant still no tools - signal exit
        logger.info("Autonomous mode: No tools used after confirmation. Exiting.")
        raise SessionCompleteException("No tools used after auto-reply confirmation")

    # First time - inject auto-reply
    logger.info(
        "Auto-reply: Assistant message had no tools. Asking for confirmation..."
    )
    yield Message(
        "user",
        "Are you sure? If you're finished, use the `complete` tool to end the session.",
        quiet=False,
    )


tool = ToolSpec(
    name="complete",
    desc="Signal that the autonomous session is finished",
    instructions="""
Use this tool to signal that you have completed your work and the autonomous session should end.

This is the proper way to finish an autonomous session instead of using sys.exit(0).
""",
    examples="""
> User: Make sure to finish when you're done
> Assistant: I'll complete the task and use the complete tool.
```complete
```
> System: Task complete. Autonomous session finished.
""",
    execute=execute_complete,
    block_types=["complete"],
    available=True,
    hooks={
        "complete": (
            HookType.GENERATION_PRE,
            complete_hook,
            1000,
        ),  # High priority - prevent generation after complete
        "auto_reply": (
            HookType.LOOP_CONTINUE,
            auto_reply_hook,
            999,
        ),  # Run after complete check (lower priority)
    },
)
