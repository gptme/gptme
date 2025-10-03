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
    Hook that checks if the complete tool was used and signals session end.

    Raises SessionCompleteException to signal the main loop to exit cleanly.
    """
    # Check last few messages for complete tool usage
    complete_used = any(
        tu.tool == "complete"
        for msg in reversed(log[-5:])
        if msg.role == "assistant"
        for tu in ToolUse.iter_from_content(msg.content)
    )

    if complete_used:
        logger.info("Complete tool detected, signaling session end")
        raise SessionCompleteException("Session completed via complete tool")


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
            HookType.MESSAGE_POST_PROCESS,
            complete_hook,
            1000,
        ),  # High priority
    },
)
