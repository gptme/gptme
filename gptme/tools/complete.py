"""Complete tool - signals that the autonomous session is finished."""

import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation
from ..message import Message
from .base import ConfirmFunc, ToolSpec, ToolUse

if TYPE_CHECKING:
    from ..logmanager import LogManager

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


def complete_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that detects complete tool call and prevents next generation.

    Runs at GENERATION_PRE (before generating response) to stop the session
    immediately after complete tool is called.

    Args:
        messages: List of conversation messages
        **kwargs: Additional arguments (workspace, manager - currently unused)

    Note: GENERATION_PRE hooks are called with messages as first positional arg,
    not manager as the Protocol suggests. This is a known type safety issue.
    """
    # Make function a generator for type checking
    if False:
        yield

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
    manager: "LogManager", interactive: bool, prompt_queue: Any
) -> Generator[Message | StopPropagation, None, None]:
    """
    Hook that implements auto-reply mechanism for autonomous operation.

    If in non-interactive mode and last assistant message had no tools,
    inject an auto-reply to ensure the assistant does work.

    This is called via LOOP_CONTINUE hook, which receives interactive and prompt_queue.

    Args:
        manager: Conversation manager with log and workspace
        interactive: Whether in interactive mode
        prompt_queue: Queue of pending prompts
    """
    # Only run in non-interactive mode
    if interactive:
        return

    # Skip if there are queued prompts
    if prompt_queue:
        return

    last_assistant_msg = next(
        (m for m in reversed(manager.log.messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    if tool_uses:
        return  # Has tools, no need to prompt

    # Count consecutive auto-replies
    auto_reply_count = 0
    for msg in reversed(manager.log.messages):
        if msg.role == "user" and "use the `complete` tool" in msg.content:
            auto_reply_count += 1
        elif msg.role == "assistant":
            # Stop counting when we hit an assistant message with tools
            if list(ToolUse.iter_from_content(msg.content)):
                break
        else:
            break

    # Exit after 2 consecutive auto-replies without tools
    if auto_reply_count >= 2:
        logger.info("Autonomous mode: No tools used after 2 confirmations. Exiting.")
        raise SessionCompleteException("No tools used after 2 auto-reply confirmations")

    # First time - inject auto-reply
    logger.info(
        "Auto-reply: Assistant message had no tools. Asking for confirmation..."
    )
    yield Message(
        "user",
        "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
        quiet=False,
    )


tool = ToolSpec(
    name="complete",
    desc="Signal that the autonomous session is finished",
    disabled_by_default=True,  # Only enable in autonomous/non-interactive sessions
    instructions="""
Use this tool to signal that you have completed your work and the autonomous session should end.

Make sure you have actually completely finished before calling this tool.
""",
    examples="""
> User: Everything done, just complete
> Assistant: I'll use the complete tool to end the session.
```complete
```
> System: Task complete. Autonomous session finished.
""",
    execute=execute_complete,
    block_types=["complete"],
    available=True,
    hooks={
        "complete": (
            HookType.GENERATION_PRE.value,
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
