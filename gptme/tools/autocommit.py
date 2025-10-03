"""
Autocommit hook tool that automatically commits changes after message processing.
"""

import logging
from collections.abc import Generator

from ..commands import CommandContext
from ..config import get_config
from ..hooks import HookType
from ..logmanager import Log
from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)


def handle_commit_command(ctx: CommandContext) -> Generator[Message, None, None]:
    """Handle the /commit command to manually trigger commit.

    Args:
        ctx: Command context with manager and confirm function

    Yields:
        Message asking LLM to review and commit changes
    """
    # Undo the command message itself
    ctx.manager.undo(1, quiet=True)

    # Import here to avoid circular dependency
    from ..util.context import autocommit

    yield autocommit()


def autocommit_on_message_complete(
    log: Log, workspace, **kwargs
) -> Generator[Message, None, None]:
    """Hook function that handles auto-commit after message processing.

    Args:
        log: The conversation log
        workspace: Workspace directory path

    Yields:
        Message asking LLM to review and commit if changes exist
    """
    # Check if autocommit is enabled
    if not get_config().get_env_bool("GPTME_AUTOCOMMIT"):
        logger.debug("Autocommit not enabled, skipping")
        return

    # Check if there are modifications
    from ..chat import check_for_modifications

    if not check_for_modifications(log):
        logger.debug("No modifications detected, skipping autocommit")
        return

    try:
        # Import here to avoid circular dependency
        from ..util.context import autocommit

        # Get autocommit message (asks LLM to review and commit)
        yield autocommit()

    except Exception as e:
        logger.exception(f"Error during autocommit: {e}")
        yield Message("system", f"Autocommit failed: {e}", hide=True)


# Tool specification
tool = ToolSpec(
    name="autocommit",
    desc="Automatically commit changes after message processing",
    instructions="""
This tool automatically commits changes made during a conversation.

When GPTME_AUTOCOMMIT=true is set, after each message is processed:
1. Checks if there are file modifications
2. If modifications exist, returns a message asking the LLM to review and commit

The tool hooks into MESSAGE_POST_PROCESS and runs with low priority
(after pre-commit checks and other validation).

To enable autocommit:
```bash
export GPTME_AUTOCOMMIT=true
```
""".strip(),
    available=True,
    hooks={
        "autocommit": (
            HookType.MESSAGE_POST_PROCESS.value,
            autocommit_on_message_complete,
            1,  # Low priority: run after pre-commit checks (priority 5)
        )
    },
    commands={
        "commit": handle_commit_command,
    },
)

__all__ = ["tool"]
