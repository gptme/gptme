"""
Autocommit hook tool that automatically provides hints for committing changes after message processing.

When GPTME_AUTOCOMMIT=true is set, after each message is processed:
1. Checks if there are file modifications
2. If modifications exist, returns a message asking the LLM to review and commit

The tool hooks into MESSAGE_POST_PROCESS and runs with low priority
(after pre-commit checks and other validation).

To enable autocommit:
```bash
export GPTME_AUTOCOMMIT=true
```
"""

import logging
import subprocess
from collections.abc import Generator
from typing import TYPE_CHECKING

from ..commands import CommandContext
from ..config import get_config
from ..hooks import HookType, StopPropagation
from ..logmanager import check_for_modifications
from ..message import Message
from .base import ToolSpec

if TYPE_CHECKING:
    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


def autocommit() -> Message:
    """
    Auto-commit changes made by gptme.

    Returns a message asking the LLM to review changes and create a commit.
    """
    try:
        # See if there are any changes to commit by checking for
        # changes, excluding untracked files.
        status_result_porcelain = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        )

        if not status_result_porcelain.stdout.strip():
            return Message("system", "No changes to commit.")

        # Get current git status
        status_result = subprocess.run(
            ["git", "status"], capture_output=True, text=True, check=True
        )

        # Get git diff to show what changed
        diff_result = subprocess.run(
            ["git", "diff", "HEAD"], capture_output=True, text=True, check=True
        )

        # Create a message for the LLM to handle the commit
        commit_prompt = f"""Pre-commit checks have passed and the following changes have been made:

```git status
{status_result.stdout}
```

```git diff HEAD
{diff_result.stdout}
```

This is a good time to review these changes and consider creating an appropriate commit:

1. Review the changes, decide which changes to include in the commit
2. Stage only the relevant files using `git add` (never use `git add .` or `git add -A` to avoid adding unintended files)
3. Create the commit using the HEREDOC format to avoid escaping issues. Both stage and commit in one go.

```shell
git add example.txt
git commit -m "$(cat <<'EOF'
Your commit message here
EOF
)"
```
"""

        return Message("system", commit_prompt)

    except subprocess.CalledProcessError as e:
        if e.returncode == -2:
            raise KeyboardInterrupt from e
        return Message(
            "system",
            f"Git operation failed (code {e.returncode}): {e.stderr or e.stdout or str(e)}",
        )
    except Exception as e:
        logger.error(f"Autocommit failed: {e}")
        return Message("system", f"Autocommit failed: {e}")


def handle_commit_command(ctx: CommandContext) -> Generator[Message, None, None]:
    """Handle the /commit command to manually trigger commit.

    Args:
        ctx: Command context with manager and confirm function

    Yields:
        Message asking LLM to review and commit changes
    """
    # Undo the command message itself
    ctx.manager.undo(1, quiet=True)

    yield autocommit()


def autocommit_on_message_complete(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook function that handles auto-commit after message processing.

    Args:
        manager: Conversation manager with log and workspace

    Yields:
        Message asking LLM to review and commit if changes exist
    """
    # Check if autocommit is enabled
    if not get_config().get_env_bool("GPTME_AUTOCOMMIT"):
        logger.debug("Autocommit not enabled, skipping")
        return

    # Check if there are modifications

    if not check_for_modifications(manager.log):
        logger.debug("No modifications detected, skipping autocommit")
        return

    try:
        # Get autocommit message (asks LLM to review and commit)
        yield autocommit()

    except Exception as e:
        logger.exception(f"Error during autocommit: {e}")
        yield Message("system", f"Autocommit failed: {e}", hide=True)


# Tool specification
# TODO: should probably be disabled by default, or at least in non-interactive modes
tool = ToolSpec(
    name="autocommit",
    desc="Automatic hints to commit changes after message processing",
    instructions="This tool will automatically provide hints to commit changes before returning control to the user".strip(),
    available=True,
    hooks={
        "autocommit": (
            HookType.MESSAGE_POST_PROCESS.value,
            autocommit_on_message_complete,
            # Low priority (1) ensures this runs after pre-commit checks (priority 5)
            # If pre-commit fails, it yields StopPropagation() to prevent autocommit from running
            1,
        )
    },
    commands={
        "commit": handle_commit_command,
    },
)

__all__ = ["tool"]
