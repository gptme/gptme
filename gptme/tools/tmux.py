"""
You can use the tmux tool to run long-lived and/or interactive applications in a tmux session. Requires tmux to be installed.

This tool is suitable to run long-running commands or interactive applications that require user input.
Examples of such commands: ``npm run dev``, ``python3 server.py``, ``python3 train.py``, etc.
It allows for inspecting pane contents and sending input.
"""

import logging
import shutil
import subprocess
from collections.abc import Generator
from time import sleep

from ..message import Message
from ..util.ask_execute import print_preview
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

logger = logging.getLogger(__name__)

# Examples of identifiers:
#   session: gptme_0
#   window: gptme_0:0
#   pane: gptme_0:0.0


def _run_tmux_command(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a tmux command with consistent logging and error handling."""
    print(" ".join(cmd))
    result = subprocess.run(
        " ".join(cmd),
        check=True,
        capture_output=True,
        text=True,
        shell=True,
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
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-t", pane_id],
        capture_output=True,
        text=True,
    )
    return result.stdout


def new_session(command: str) -> Message:
    _max_session_id = 0
    for session in get_sessions():
        if session.startswith("gptme_"):
            _max_session_id = max(_max_session_id, int(session.split("_")[1]))
    session_id = f"gptme_{_max_session_id + 1}"
    # cmd = ["tmux", "new-session", "-d", "-s", session_id, command]
    cmd = ["tmux", "new-session", "-d", "-s", session_id, "bash"]
    _run_tmux_command(cmd)

    # set session size
    cmd = ["tmux", "resize-window", "-t", session_id, "-x", "120", "-y", "40"]
    _run_tmux_command(cmd)

    cmd = ["tmux", "send-keys", "-t", session_id, command, "Enter"]
    _run_tmux_command(cmd)

    # sleep 1s and capture output
    sleep(1)
    output = _capture_pane(f"{session_id}")
    return Message(
        "system",
        f"""Running `{command.strip("'")}` in session {session_id}.\n```output\n{output}\n```""",
    )


def send_keys(pane_id: str, keys: str) -> Message:
    if not pane_id.startswith("gptme_"):
        pane_id = f"gptme_{pane_id}"
    result = subprocess.run(
        f"tmux send-keys -t {pane_id} {keys}",
        shell=True,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return Message(
            "system", f"Failed to send keys to tmux pane `{pane_id}`: {result.stderr}"
        )
    sleep(1)
    output = _capture_pane(pane_id)
    return Message(
        "system", f"Sent '{keys}' to pane `{pane_id}`\n```output\n{output}\n```"
    )


def inspect_pane(pane_id: str) -> Message:
    content = _capture_pane(pane_id)
    return Message(
        "system",
        f"""Pane content:
{ToolUse("output", [], content).to_output()}""",
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
            yield inspect_pane(_args)
        elif command == "kill-session":
            yield kill_session(_args)
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
- kill-session <session_id>: Terminate the specified tmux session
- list-sessions: Show all active tmux sessions
"""
# TODO: implement smart-wait, where we wait for n seconds and then until output is stable
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
