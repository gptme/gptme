"""
Restart the gptme process.

This tool allows restarting gptme from within a conversation, which can be useful
for applying configuration changes, reloading tools, or recovering from state issues.
"""

import logging
import os
import sys
from collections.abc import Generator

from ..message import Message
from .base import ConfirmFunc, ToolSpec

logger = logging.getLogger(__name__)

# Track if we've already triggered a restart in this process
_triggered_restart = False


def _do_restart(conversation_name: str | None = None):
    """Restart gptme by manually triggering cleanup and then execing.

    This approach:
    1. Manually triggers all atexit handlers
    2. Performs additional cleanup (close files, etc.)
    3. Directly replaces the current process with a new gptme instance
    4. Preserves stdin/stdout/stderr so terminal connection is maintained

    Args:
        conversation_name: The name of the current conversation to resume
    """
    import atexit

    # Build restart command with explicit conversation name
    # Use sys.argv[0] (gptme script) not sys.executable (python interpreter)
    restart_args = sys.argv[:]

    # Ensure we have the conversation name in the args
    if conversation_name:
        # Remove any existing --name or --resume args and their values
        filtered_args = []
        skip_next = False
        for arg in restart_args:
            if skip_next:
                skip_next = False
                continue
            if arg in ["--name", "-r", "--resume"]:
                skip_next = True  # Skip this flag and the next argument (its value)
                continue
            filtered_args.append(arg)
        restart_args = filtered_args

        # Add explicit --name to resume this conversation
        restart_args.extend(["--name", conversation_name])

    logger.info(f"Restarting with: {' '.join(restart_args)}")

    # Manually trigger all atexit handlers to ensure proper cleanup
    # This includes releasing the LogManager lock via its registered handler
    try:
        atexit._run_exitfuncs()
    except Exception as e:
        logger.warning(f"Error during atexit cleanup: {e}")

    # Additional cleanup: flush and close output streams
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    # Replace current process with new gptme instance
    # stdin/stdout/stderr are preserved, so terminal connection is maintained
    os.execv(sys.argv[0], restart_args)


def execute_restart(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute restart by confirming intent.

    The actual restart happens in the restart_hook (GENERATION_PRE),
    after all messages have been saved to the log.
    """
    global _triggered_restart

    if not confirm("Restart gptme? This will exit and restart the process."):
        yield Message("system", "Restart cancelled.")
        return

    # Mark that restart has been confirmed
    _triggered_restart = True

    # Just yield a confirmation message
    # The actual restart will happen in the GENERATION_PRE hook
    yield Message("system", "Restarted gptme. Resuming conversation...")


def restart_hook(
    messages: list[Message],
    **kwargs,
) -> Generator[Message, None, None]:
    """
    Hook that detects restart tool call and performs the restart.

    Runs at GENERATION_PRE (before generating response) to restart
    immediately after the restart tool is called.

    By this point, all messages (including the assistant's restart message
    and the system confirmation) have been saved to the log.
    """
    # Make function a generator for type checking
    if False:
        yield

    if not messages:
        return

    # Look for restart tool call in the last assistant message
    last_assistant_msg = next(
        (m for m in reversed(messages) if m.role == "assistant"), None
    )
    if not last_assistant_msg:
        return

    # Check if the assistant called the restart tool
    from .base import ToolUse

    global _triggered_restart

    # Only proceed if restart was confirmed (flag set by execute_restart)
    if not _triggered_restart:
        return

    tool_uses = list(ToolUse.iter_from_content(last_assistant_msg.content))
    for tool_use in tool_uses:
        if tool_use.tool == "restart":
            logger.info("Restart confirmed and detected, restarting now...")

            # Get conversation name
            conversation_name = None
            try:
                from ..logmanager import LogManager

                log_manager = LogManager.get_current_log()
                if log_manager:
                    conversation_name = log_manager.logfile.parent.name
                    logger.info(f"Restarting with conversation: {conversation_name}")

                    # Ensure everything is synced to disk
                    log_manager.write(sync=True)
            except Exception as e:
                logger.warning(f"Error preparing restart: {e}")

            # Perform the restart
            _do_restart(conversation_name)

            # This line should never be reached
            sys.exit(1)


tool = ToolSpec(
    name="restart",
    desc="Restart the gptme process",
    instructions="""
Restart the gptme process, useful for:
- Applying configuration changes that require a restart
- Reloading tools after code modifications
- Recovering from state issues
- Testing tool initialization

The restart preserves the current conversation by reloading it from disk.
All command-line arguments are preserved in the new process.

This tool is disabled by default and must be explicitly enabled with `--tools restart`.
""",
    examples="""
> User: restart gptme to apply the config changes
> Assistant: I'll restart gptme now.
```restart

```
> System: Restarting gptme...
(gptme restarts and conversation continues)

> User: can you restart?
> Assistant: I'll restart the gptme process.
```restart

```
> System: Restarting gptme...
""",
    execute=execute_restart,
    block_types=["restart"],
    disabled_by_default=True,
    hooks={
        "restart": (
            "generation_pre",  # HookType.GENERATION_PRE.value
            restart_hook,
            1000,  # High priority - restart before next generation
        ),
    },
)
