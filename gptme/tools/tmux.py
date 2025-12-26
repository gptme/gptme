"""
You can use the tmux tool to run long-lived and/or interactive applications in a tmux session. Requires tmux to be installed.

This tool is suitable to run long-running commands or interactive applications that require user input.
Examples of such commands: ``npm run dev``, ``python3 server.py``, ``python3 train.py``, etc.
It allows for inspecting pane contents and sending input.
"""

import functools
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Generator
from pathlib import Path
from time import sleep

from ..message import Message
from ..util.ask_execute import print_preview
from ..util.output_storage import save_large_output
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

logger = logging.getLogger(__name__)

# Default truncation settings for pane output
# These can be adjusted via function parameters if needed
DEFAULT_PRE_LINES = 50  # Lines to show at start
DEFAULT_POST_LINES = (
    150  # Lines to show at end (more recent output is usually more relevant)
)
MAX_OUTPUT_LINES = 200  # Total max lines before truncation kicks in


def _truncate_output(
    content: str,
    pre_lines: int = DEFAULT_PRE_LINES,
    post_lines: int = DEFAULT_POST_LINES,
    logdir: Path | None = None,
    context: str | None = None,
) -> str:
    """Truncate long output to prevent context overflow.

    If content exceeds pre_lines + post_lines, truncates the middle and
    optionally saves the full output to a file.

    Args:
        content: The full output content
        pre_lines: Number of lines to keep at the start
        post_lines: Number of lines to keep at the end
        logdir: Directory to save full output (if provided and truncation occurs)
        context: Context string for saved file (e.g., "inspect-pane gptme_1")

    Returns:
        Truncated content with message about truncation, or original if short enough
    """
    lines = content.split("\n")
    total_lines = len(lines)

    # No truncation needed if output is short enough
    if total_lines <= pre_lines + post_lines:
        return content

    # Calculate truncated line count
    truncated_count = total_lines - pre_lines - post_lines

    # Save full output to file if logdir provided
    saved_path = None
    if logdir:
        _, saved_path = save_large_output(
            content=content,
            logdir=logdir,
            output_type="tmux",
            command_info=context,
        )

    # Build truncation message
    truncation_msg = f"... ({truncated_count} lines truncated"
    if saved_path:
        truncation_msg += f", full output saved to {saved_path}"
    truncation_msg += ") ..."

    # Combine pre + truncation message + post
    truncated_lines = lines[:pre_lines] + [truncation_msg] + lines[-post_lines:]
    return "\n".join(truncated_lines)


# Examples of identifiers:
#   session: gptme_0
#   window: gptme_0:0
#   pane: gptme_0:0.0


def _run_tmux_command(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a tmux command with consistent logging and error handling."""
    print(" ".join(cmd))
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    print(result.stdout, result.stderr)
    return result


def get_sessions() -> list[str]:
    output = subprocess.run(
        ["tmux", "has"],
        capture_output=True,
        text=True,
    )
    if output.returncode != 0:
        return []
    output = subprocess.run(
        ["tmux", "list-sessions"],
        capture_output=True,
        text=True,
    )
    assert output.returncode == 0
    return [session.split(":")[0] for session in output.stdout.split("\n") if session]


def _capture_pane(pane_id: str) -> str:
    """Capture the content of a tmux pane including scrollback history."""
    result = subprocess.run(
        # -p: print to stdout
        # -S -: start from beginning of scrollback
        # -E -: end at bottom of scrollback
        ["tmux", "capture-pane", "-p", "-S", "-", "-E", "-", "-t", pane_id],
        capture_output=True,
        text=True,
    )
    # Strip trailing whitespace but preserve content
    return result.stdout.rstrip()


# decorator for lock
# Lock for new_session to avoid race conditions when creating sessions concurrently
_new_session_lock = threading.Lock()


def flock(lock):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with lock:
                return func(*args, **kwargs)

        return wrapper

    return decorator


# @flock to avoid race conditions when creating new sessions concurrently
# as can happen in tests, causing flakiness
@flock(_new_session_lock)
def new_session(command: str) -> Message:
    # Retry loop to handle race conditions where session already exists
    max_retries = 5
    session_id = ""
    for attempt in range(max_retries):
        _max_session_id = 0
        for session in get_sessions():
            # Only parse sessions matching exact pattern "gptme_N" (not gptme_gw0_*, gptme_test_*, etc.)
            if session.startswith("gptme_"):
                parts = session.split("_")
                if len(parts) == 2 and parts[1].isdigit():
                    _max_session_id = max(_max_session_id, int(parts[1]))
        # Add attempt offset to avoid collision on retry
        session_id = f"gptme_{_max_session_id + 1 + attempt}"
        try:
            cmd = ["tmux", "new-session", "-d", "-s", session_id, "bash"]
            _run_tmux_command(cmd)
            break  # Success, exit retry loop
        except subprocess.CalledProcessError:
            if attempt == max_retries - 1:
                raise  # Re-raise on last attempt
            # Session might already exist (race condition), retry with higher ID
            sleep(0.1)  # Brief delay before retry
            continue

    # set session size
    cmd = ["tmux", "resize-window", "-t", session_id, "-x", "120", "-y", "40"]
    _run_tmux_command(cmd)

    cmd = ["tmux", "send-keys", "-t", session_id, command, "Enter"]
    _run_tmux_command(cmd)

    # sleep 1s and capture output
    sleep(1)
    output = _capture_pane(f"{session_id}")
    # Truncate output if too long
    truncated_output = _truncate_output(
        output,
        context=f"new-session {command[:50]}",
    )
    return Message(
        "system",
        f"""Running `{command.strip("'")}` in session {session_id}.\n```output\n{truncated_output}\n```""",
    )


def send_keys(pane_id: str, keys: str) -> Message:
    import shlex

    if not pane_id.startswith("gptme_"):
        pane_id = f"gptme_{pane_id}"
    # Parse keys into separate arguments to avoid shell injection
    # shlex.split properly handles quoted strings and escapes
    try:
        key_args = shlex.split(keys)
    except ValueError:
        # Fall back to single argument if shlex can't parse (unmatched quotes, etc.)
        key_args = [keys]
    result = subprocess.run(
        ["tmux", "send-keys", "-t", pane_id, *key_args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return Message(
            "system", f"Failed to send keys to tmux pane `{pane_id}`: {result.stderr}"
        )
    sleep(1)
    output = _capture_pane(pane_id)
    # Truncate output if too long
    truncated_output = _truncate_output(
        output,
        context=f"send-keys {pane_id}",
    )
    return Message(
        "system",
        f"Sent '{keys}' to pane `{pane_id}`\n```output\n{truncated_output}\n```",
    )


def inspect_pane(pane_id: str, logdir: Path | None = None) -> Message:
    """Inspect the content of a tmux pane.

    Args:
        pane_id: The tmux pane ID to inspect
        logdir: Optional directory to save full output if truncated

    Returns:
        Message with pane content (truncated if too long)
    """
    content = _capture_pane(pane_id)

    # Truncate if content is too long to prevent context overflow
    truncated = _truncate_output(
        content,
        logdir=logdir,
        context=f"inspect-pane {pane_id}",
    )

    return Message(
        "system",
        f"""Pane content:
{ToolUse("output", [], truncated).to_output()}""",
    )


def kill_session(session_id: str) -> Message:
    if not session_id.startswith("gptme_"):
        session_id = f"gptme_{session_id}"
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_id],
        check=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return Message(
            "system",
            f"Failed to kill tmux session with ID {session_id}: {result.stderr}",
        )
    return Message("system", f"Killed tmux session with ID {session_id}")


def list_sessions() -> Message:
    sessions = get_sessions()
    return Message("system", f"Active tmux sessions: {sessions}")


def wait_for_output(
    session_id: str,
    timeout: int = 60,
    stable_time: int = 3,
    logdir: Path | None = None,
) -> Message:
    """Wait for command output to stabilize in a tmux session.

    Monitors the pane output and waits until it remains unchanged for
    `stable_time` seconds, or until `timeout` is reached.

    Args:
        session_id: The tmux session ID to monitor
        timeout: Maximum time to wait in seconds (default: 60)
        stable_time: Seconds of unchanged output to consider stable (default: 3)

    Returns:
        Message with the final output and status
    """
    if not session_id.startswith("gptme_"):
        session_id = f"gptme_{session_id}"

    start_time = time.time()
    last_output: str | None = None  # None means no output captured yet
    last_change_time = start_time
    poll_interval = 0.5  # Poll more frequently for responsiveness

    while True:
        elapsed = time.time() - start_time
        current_output = _capture_pane(session_id)

        # Check if output changed (comparing stripped content)
        if last_output is None or current_output != last_output:
            last_output = current_output
            last_change_time = time.time()

        # Check if output has been stable long enough
        # Only declare stable if we have meaningful output (not just empty/whitespace)
        stable_duration = time.time() - last_change_time
        if stable_duration >= stable_time:
            # Truncate output if too long
            truncated_output = _truncate_output(
                current_output,
                context=f"wait {session_id}",
                logdir=logdir,
            )
            return Message(
                "system",
                f"Session `{session_id}` output stabilized after {elapsed:.1f}s "
                f"(stable for {stable_duration:.1f}s).\n"
                f"```output\n{truncated_output}\n```",
            )

        # Check timeout
        if elapsed >= timeout:
            # Truncate output if too long
            truncated_output = _truncate_output(
                current_output,
                context=f"wait {session_id} (timeout)",
                logdir=logdir,
            )
            return Message(
                "system",
                f"Session `{session_id}` timed out after {timeout}s "
                f"(output still changing).\n"
                f"```output\n{truncated_output}\n```\n"
                f"Session still active. Use `kill-session {session_id}` to terminate "
                f"or `send-keys {session_id} C-c` to interrupt.",
            )

        # Poll at configured interval
        sleep(poll_interval)


def execute_tmux(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Executes a command in tmux and returns the output."""

    # Get the command string
    cmd = ""
    if code is not None and args is not None:
        assert not args
        cmd = code.strip()
    elif kwargs is not None:
        cmd = kwargs.get("command", "")

    # Split into multiple commands, handling quoted strings and newlines
    commands = []
    current = []
    in_quotes = False
    quote_char = None

    for char in cmd:
        if char in ["'", '"']:
            if not in_quotes:
                in_quotes = True
                quote_char = char
            elif quote_char == char:
                in_quotes = False
                quote_char = None
            current.append(char)
        elif char == "\n" and not in_quotes:
            if current:
                commands.append("".join(current).strip())
                current = []
        elif char == ";" and not in_quotes:
            if current:
                commands.append("".join(current).strip())
                current = []
        else:
            current.append(char)

    if current:
        commands.append("".join(current).strip())

    # Preview all commands
    preview = "\n".join(commands)
    print_preview(preview, "bash", copy=True)
    if not confirm("Execute commands?"):
        yield Message(
            "system", "Execution aborted: user chose not to run the commands."
        )
        return

    # Execute each command
    for cmd in commands:
        parts = cmd.split(maxsplit=1)
        if not parts:
            continue

        command = parts[0]
        if command == "list-sessions":
            yield list_sessions()
            continue

        if len(parts) < 2:
            yield Message("system", f"Error: Missing arguments for command: {command}")
            continue

        _args = parts[1]
        if command == "new-session":
            yield new_session(_args)
        elif command == "send-keys":
            pane_id, keys = _args.split(maxsplit=1)
            yield send_keys(pane_id, keys)
        elif command == "inspect-pane":
            # Use default cache directory for saving full output when truncated
            from platformdirs import user_cache_dir

            cache_dir = Path(user_cache_dir("gptme")) / "tmux-output"
            cache_dir.mkdir(parents=True, exist_ok=True)
            yield inspect_pane(_args, logdir=cache_dir)
        elif command == "kill-session":
            yield kill_session(_args)
        elif command == "wait":
            # Parse optional timeout and stable_time from args
            # Format: wait <session_id> [timeout] [stable_time]
            wait_parts = _args.split()
            wait_session_id = wait_parts[0]
            wait_timeout = int(wait_parts[1]) if len(wait_parts) > 1 else 60
            wait_stable = int(wait_parts[2]) if len(wait_parts) > 2 else 3
            # Use default cache directory for saving full output when truncated
            from platformdirs import user_cache_dir

            cache_dir = Path(user_cache_dir("gptme")) / "tmux-output"
            cache_dir.mkdir(parents=True, exist_ok=True)
            yield wait_for_output(
                wait_session_id, wait_timeout, wait_stable, logdir=cache_dir
            )
        else:
            yield Message("system", f"Error: Unknown command: {command}")


instructions = """
You can use the tmux tool to run long-lived and/or interactive applications in a tmux session.

This tool is suitable to run long-running commands or interactive applications that require user input.
Examples of such commands are: `npm run dev`, `npm create vue@latest`, `python3 server.py`, `python3 train.py`, etc.

Available commands:
- new-session <command>: Start a new tmux session with the given command
- send-keys <session_id> <keys> [<keys>]: Send keys to the specified session
- inspect-pane <session_id>: Show the current content of the specified pane
- wait <session_id> [timeout] [stable_time]: Wait for output to stabilize (default: 60s timeout, 3s stable)
- kill-session <session_id>: Terminate the specified tmux session
- list-sessions: Show all active tmux sessions
"""
# TODO: change the "commands" to Python functions registered with the Python tool?


def examples(tool_format):
    escaped_hello_world = "'print(\"Hello, world!\")'"
    all_examples = f"""
#### Managing a dev server

> User: Start the dev server
> Assistant: Certainly! To start the dev server we should use tmux:
{ToolUse("tmux", [], "new-session 'npm run dev'").to_output(tool_format)}
> System: Running `npm run dev` in session gptme_1

> User: Can you show me the current content of the pane?
> Assistant: Of course! Let's inspect the pane content:
{ToolUse("tmux", [], "inspect-pane gptme_1").to_output(tool_format)}
> System:
{ToolUse("output", [], "Server is running on localhost:5600").to_output()}

> User: Stop the dev server
> Assistant: I'll send 'Ctrl+C' to the pane to stop the server:
{ToolUse("tmux", [], "send-keys 0 C-c").to_output(tool_format)}
> System: Sent 'C-c' to pane 0

#### Get info from ncurses applications

> User: start top and give me a summary
> Assistant: Sure! Let's start the top command in a tmux session:
{ToolUse("tmux", [], "new-session 'top'").to_output(tool_format)}
> System: Running `top` in session gptme_1.
{ToolUse("output", [], "(output from top shown here)").to_output()}
> Assistant: The load is...

#### Send keys to a session

> User: start ipython
> Assistant: Let's start an ipython session:
{ToolUse("tmux", [], "new-session 'ipython'").to_output(tool_format)}
> System: Running `ipython` in session 2.
{ToolUse("output", [], "(output from ipython shown here)").to_output()}
> User: Run 'print("Hello, world!")' in the ipython session
{ToolUse("tmux", [], f'send-keys 2 {escaped_hello_world} Enter').to_output(tool_format)}

#### Listing active sessions

> User: List all active tmux sessions
{ToolUse("tmux", [], "list-sessions").to_output(tool_format)}
> System: Active tmux sessions ['0', 'gptme_1']

#### Waiting for command completion

> User: Run a build and wait for it to finish
> Assistant: I'll start the build in tmux and wait for it to complete:
{ToolUse("tmux", [], "new-session 'npm run build'").to_output(tool_format)}
> System: Running `npm run build` in session gptme_1.
> Assistant: Now let's wait for the build to finish:
{ToolUse("tmux", [], "wait gptme_1 120").to_output(tool_format)}
> System: Session `gptme_1` output stabilized after 45.2s (stable for 3.0s).
```output
...build output...
Build completed successfully!
```

#### Ending a session

> User: I changed my mind
> Assistant: No problem! Let's kill the session and start over:
> Assistant:
{ToolUse("tmux", [], "kill-session gptme_1").to_output(tool_format)}
> System: Killed tmux session with ID gptme_1
"""
    # we want to skip the last two examples in prompting
    return "####".join(all_examples.split("####")[:-2])


tool = ToolSpec(
    name="tmux",
    desc="Executes shell commands in a tmux session",
    instructions=instructions,
    examples=examples,
    execute=execute_tmux,
    block_types=["tmux"],
    available=shutil.which("tmux") is not None,
    parameters=[
        Parameter(
            name="command",
            type="string",
            description="The command and arguments to execute.",
            required=True,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
