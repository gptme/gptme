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


# Flags that take a value (next argument)
_FLAGS_WITH_VALUES = {
    "--name",
    "-m",
    "--model",
    "-w",
    "--workspace",
    "--agent-path",
    "--system",
    "-t",
    "--tools",
    "--tool-format",
    "--context-mode",
    "--context-include",
    "--output-schema",
}


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

    # Build restart command, filtering out positional arguments (prompts)
    # since they are already in the conversation log (Issue #1011)
    # Use sys.argv[0] (gptme script) not sys.executable (python interpreter)
    filtered_args = [sys.argv[0]]  # Keep script name
    skip_next = False
    i = 1

    skip_value = False  # True when we need to skip the next value (for --name)
    while i < len(sys.argv):
        arg = sys.argv[i]

        if skip_next:
            # This arg is a value for the previous flag, keep it
            filtered_args.append(arg)
            skip_next = False
            i += 1
            continue

        if skip_value:
            # This arg is a value for --name/--resume, skip it
            skip_value = False
            i += 1
            continue

        if arg.startswith("-"):
            # This is a flag
            # Flags that are persisted to the chat config and loaded on resume
            # These should NOT be re-passed on restart since they're in the conversation config
            _PERSISTED_FLAGS = {
                "--name",  # we add explicitly with conversation name
                "-r",
                "--resume",  # boolean flags for resuming
                "-m",
                "--model",  # model is persisted
                "-t",
                "--tools",  # tools allowlist is persisted
                "--tool-format",  # tool format is persisted
                "--stream",
                "--no-stream",  # streaming preference
                "-n",
                "--non-interactive",  # interactive mode
                "--agent-path",  # agent path
                "-w",
                "--workspace",  # workspace path
                "--multi-tool",
                "--no-multi-tool",  # multi-tool mode
                "--context-mode",  # context mode
                "--context-include",  # context includes
            }
            if arg in _PERSISTED_FLAGS:
                # Skip flags that are persisted to chat config
                if arg in _FLAGS_WITH_VALUES:
                    skip_value = True  # Also skip the value
                # Don't add to filtered_args
            elif arg in _FLAGS_WITH_VALUES:
                # Flag takes a value, keep flag and mark to keep next arg
                filtered_args.append(arg)
                skip_next = True
            elif "=" in arg:
                # Flag with inline value (--flag=value), keep it
                # But skip if it's a persisted flag (loaded from chat config)
                flag_name = arg.split("=")[0]
                _PERSISTED_FLAGS_INLINE = {
                    "--name",
                    "--resume",
                    "-r",
                    "-m",
                    "--model",
                    "-t",
                    "--tools",
                    "--tool-format",
                    "--stream",
                    "--no-stream",
                    "-n",
                    "--non-interactive",
                    "--agent-path",
                    "-w",
                    "--workspace",
                    "--multi-tool",
                    "--no-multi-tool",
                    "--context-mode",
                    "--context-include",
                }
                if flag_name not in _PERSISTED_FLAGS_INLINE:
                    filtered_args.append(arg)
            else:
                # Boolean flag (no value), keep it
                filtered_args.append(arg)
        # else: positional argument (prompt) - skip it (Issue #1011)

        i += 1

    restart_args = filtered_args

    # Ensure we have the conversation name in the args
    if conversation_name:
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
