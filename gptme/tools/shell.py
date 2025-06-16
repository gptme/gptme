"""
The assistant can execute shell commands with bash by outputting code blocks with `shell` as the language.
"""

import atexit
import logging
import os
import re
import select
import signal
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import bashlex

from ..message import Message
from ..util import get_installed_programs, get_tokenizer
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ConfirmFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

logger = logging.getLogger(__name__)


allowlist_commands = [
    "ls",
    "stat",
    "cd",
    "cat",
    "pwd",
    "echo",
    "head",
    "find",
    "rg",
    "ag",
    "tail",
    "grep",
    "wc",
    "sort",
    "uniq",
    "cut",
    "file",
    "which",
    "type",
    "tree",
    "du",
    "df",
]

candidates = (
    # platform-specific
    "brew",
    "apt-get",
    "pacman",
    # common and useful
    "ffmpeg",
    "magick",
    "pandoc",
    "git",
    "docker",
)


shell_programs_str = "\n".join(
    f"- {prog}" for prog in sorted(get_installed_programs(candidates))
)
is_macos = sys.platform == "darwin"


instructions = f"""
The given command will be executed in a stateful bash shell.
The shell tool will respond with the output of the execution.

These programs are available, among others:
{shell_programs_str}
""".strip()

instructions_format: dict[str, str] = {}


def examples(tool_format):
    return f"""
> User: list the current directory
> Assistant: To list the files in the current directory, use `ls`:
{ToolUse("shell", [], "ls").to_output(tool_format)}
> System: Ran command: `ls`
{ToolUse("shell", [], '''
file1.txt
file2.txt
'''.strip()).to_output()}

#### The assistant can learn context by exploring the filesystem

> User: learn about the project
> Assistant: Lets start by checking the files
{ToolUse("shell", [], "git ls-files").to_output(tool_format)}
> System:
{ToolUse("stdout", [], '''
README.md
main.py
'''.strip()).to_output()}
> Assistant: Now lets check the README
{ToolUse("shell", [], "cat README.md").to_output(tool_format)}
> System:
{ToolUse("stdout", [], "(contents of README.md)").to_output()}
> Assistant: Now we check main.py
{ToolUse("shell", [], "cat main.py").to_output(tool_format)}
> System:
{ToolUse("stdout", [], "(contents of main.py)").to_output()}
> Assistant: The project is...


#### Create vue project

> User: Create a new vue project with typescript and pinia named fancy-project
> Assistant: Sure! Let's create a new vue project with TypeScript and Pinia named fancy-project:
{ToolUse("shell",
    [],
    "npm init vue@latest fancy-project --yes -- --typescript --pinia"
).to_output()}
> System:
{ToolUse("stdout", [], '''
> npx
> create-vue

Vue.js - The Progressive JavaScript Framework

Scaffolding project in ./fancy-project...
'''.strip()).to_output()}

#### Proper quoting for complex content

> User: add a comment with backticks and special characters
> Assistant: When passing complex content with special characters, use single quotes to prevent shell interpretation:
{ToolUse("shell", [], "echo 'Content with `backticks` and $variables that should not be interpreted' > example.txt").to_output(tool_format)}
""".strip()


class ShellSession:
    process: subprocess.Popen
    stdout_fd: int
    stderr_fd: int
    delimiter: str

    def __init__(self) -> None:
        self._init()

        # close on exit
        atexit.register(self.close)

    def _init(self):
        self.process = subprocess.Popen(
            ["bash"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,  # Unbuffered
            universal_newlines=True,
        )
        self.stdout_fd = self.process.stdout.fileno()  # type: ignore
        self.stderr_fd = self.process.stderr.fileno()  # type: ignore
        self.delimiter = "END_OF_COMMAND_OUTPUT"

        # set GIT_PAGER=cat
        self.run("export PAGER=")
        self.run("export GH_PAGER=")
        self.run("export GIT_PAGER=cat")
        # make Python output unbuffered by default for better UX
        self.run("export PYTHONUNBUFFERED=1")

    def run(self, code: str, output=True) -> tuple[int | None, str, str]:
        """Runs a command in the shell and returns the output."""
        commands = split_commands(code)
        res_code: int | None = None
        res_stdout, res_stderr = "", ""
        for cmd in commands:
            res_cur = self._run(cmd, output=output)
            res_code = res_cur[0]
            res_stdout += res_cur[1]
            res_stderr += res_cur[2]
            if res_code != 0:
                return res_code, res_stdout, res_stderr
        return res_code, res_stdout, res_stderr

    def _run(self, command: str, output=True, tries=0) -> tuple[int | None, str, str]:
        assert self.process.stdin

        # run the command
        full_command = f"{command}\n"
        full_command += f"echo ReturnCode:$? {self.delimiter}\n"
        try:
            self.process.stdin.write(full_command)
        except BrokenPipeError:
            # process has died
            if tries == 0:
                # log warning and restart, once
                logger.warning("Warning: shell process died, restarting")
                self.restart()
                return self._run(command, output=output, tries=tries + 1)
            else:
                raise

        self.process.stdin.flush()

        stdout: list[str] = []
        stderr: list[str] = []
        return_code: int | None = None

        try:
            while True:
                rlist, _, _ = select.select([self.stdout_fd, self.stderr_fd], [], [])
                for fd in rlist:
                    assert fd in [self.stdout_fd, self.stderr_fd]
                    # We use a higher value, because there is a bug which leads to spaces at the boundary
                    # 2**12 = 4096
                    # 2**16 = 65536
                    data = os.read(fd, 2**16).decode("utf-8")
                    lines = data.splitlines(keepends=True)
                    re_returncode = re.compile(r"ReturnCode:(\d+)")
                    for line in lines:
                        if "ReturnCode:" in line and self.delimiter in line:
                            if match := re_returncode.search(line):
                                return_code = int(match.group(1))
                            # if command is cd and successful, we need to change the directory
                            if command.startswith("cd ") and return_code == 0:
                                ex, pwd, _ = self._run("pwd", output=False)
                                assert ex == 0
                                os.chdir(pwd.strip())
                            return (
                                return_code,
                                "".join(stdout).strip(),
                                "".join(stderr).strip(),
                            )
                        if fd == self.stdout_fd:
                            stdout.append(line)
                            if output:
                                print(line, end="", file=sys.stdout)
                        elif fd == self.stderr_fd:
                            stderr.append(line)
                            if output:
                                print(line, end="", file=sys.stderr)
        except KeyboardInterrupt:
            # Clear line after ^C to avoid leaving a hanging line
            print()
            # Handle interrupt at the source - return partial output and re-raise
            logger.info("Process interrupted during output reading")
            # Return partial output with a special return code to indicate interruption
            partial_stdout = "".join(stdout).strip()
            partial_stderr = "".join(stderr).strip()
            # Use -999 as a special code to indicate interruption
            raise KeyboardInterrupt((partial_stdout, partial_stderr)) from None

    def close(self):
        assert self.process.stdin
        self.process.stdin.close()
        self.process.terminate()
        self.process.wait(timeout=0.2)
        self.process.kill()

    def restart(self):
        self.close()
        self._init()


_shell: ShellSession | None = None


def get_shell() -> ShellSession:
    global _shell
    if _shell is None:
        # init shell
        _shell = ShellSession()
    return _shell


# used in testing
def set_shell(shell: ShellSession) -> None:
    global _shell
    _shell = shell


# NOTE: This does not handle control flow words like if, for, while.
cmd_regex = re.compile(r"(?:^|[|&;]|\|\||&&|\n)\s*([^\s|&;]+)")


def get_shell_command(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> str:
    """Get the shell command from code/args/kwargs."""
    if code is not None and args is not None:
        assert not args
        cmd = code.strip()
        if cmd.startswith("$ "):
            cmd = cmd[len("$ ") :]
    elif kwargs is not None:
        cmd = kwargs.get("command", "")
    else:
        raise ValueError("No command provided")
    return cmd


def preview_shell(cmd: str, _: Path | None) -> str:
    """Prepare preview for shell command."""
    return cmd


def is_allowlisted(cmd: str) -> bool:
    for match in cmd_regex.finditer(cmd):
        for group in match.groups():
            if group and group not in allowlist_commands:
                return False
    return True


def _format_shell_output(
    cmd: str,
    stdout: str,
    stderr: str,
    returncode: int | None,
    interrupted: bool,
    allowlisted: bool,
    pwd_changed: bool,
    current_cwd: str,
) -> str:
    """Format shell command output into a message."""
    # Apply shortening logic
    stdout = _shorten_stdout(stdout, pre_tokens=2000, post_tokens=8000)
    stderr = _shorten_stdout(stderr, pre_tokens=2000, post_tokens=2000)

    # Format header
    if interrupted:
        header = "Command interrupted"
    else:
        header = f"Ran {'allowlisted ' if allowlisted else ''}command"

    msg = _format_block_smart(header, cmd, lang="bash") + "\n\n"

    # Add output
    if stdout:
        msg += _format_block_smart("", stdout, "stdout") + "\n\n"
    if stderr:
        msg += _format_block_smart("", stderr, "stderr") + "\n\n"
    if not stdout and not stderr:
        if interrupted:
            msg += "No output before interruption\n"
        else:
            msg += "No output\n"

    # Add status info
    if interrupted:
        if returncode is not None:
            msg += f"Process interrupted (return code: {returncode})\n"
        else:
            msg += "Process interrupted\n"
    elif returncode:
        msg += f"Return code: {returncode}\n"

    if pwd_changed:
        msg += f"Working directory changed to: {current_cwd}"

    return msg


def execute_shell_impl(
    cmd: str, _: Path | None, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Execute shell command and format output."""
    shell = get_shell()
    allowlisted = is_allowlisted(cmd)
    prev_cwd = os.getcwd()

    try:
        returncode, stdout, stderr = shell.run(cmd)
        interrupted = False
    except KeyboardInterrupt as e:
        # Extract partial output and handle subprocess termination
        stdout = stderr = ""
        if e.args and isinstance(e.args[0], tuple) and len(e.args[0]) == 2:
            stdout, stderr = e.args[0]

        # Terminate subprocess gracefully
        logger.info("Shell command interrupted, sending SIGINT to subprocess")
        try:
            shell.process.send_signal(signal.SIGINT)
            shell.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.info("Process didn't exit gracefully, terminating")
            shell.process.terminate()
            try:
                shell.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                logger.info("Process didn't terminate, killing")
                shell.process.kill()
                shell.process.wait()
        except Exception:
            pass

        returncode = shell.process.returncode
        interrupted = True
    except Exception as e:
        raise ValueError(f"Shell error: {e}") from None

    # Format and yield output
    current_cwd = os.getcwd()
    pwd_changed = prev_cwd != current_cwd

    msg = _format_shell_output(
        cmd,
        stdout,
        stderr,
        returncode,
        interrupted,
        allowlisted,
        pwd_changed,
        current_cwd,
    )
    yield Message("system", msg)

    if interrupted:
        raise KeyboardInterrupt() from None


def get_path_fn(*args, **kwargs) -> Path | None:
    return None


def execute_shell(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Executes a shell command and returns the output."""
    cmd = get_shell_command(code, args, kwargs)

    # Skip confirmation for allowlisted commands
    if is_allowlisted(cmd):
        yield from execute_shell_impl(cmd, None, lambda _: True)
    else:
        yield from execute_with_confirmation(
            cmd,
            args,
            kwargs,
            confirm,
            execute_fn=execute_shell_impl,
            get_path_fn=get_path_fn,
            preview_fn=preview_shell,
            preview_lang="bash",
            confirm_msg="Run command?",
            allow_edit=True,
        )


def _format_block_smart(header: str, cmd: str, lang="") -> str:
    # prints block as a single line if it fits, otherwise as a code block
    s = ""
    if header:
        s += f"{header}:"
    if len(cmd.split("\n")) == 1:
        s += f" `{cmd}`"
    else:
        s += f"\n```{lang}\n{cmd}\n```"
    return s


def _shorten_stdout(
    stdout: str,
    pre_lines=None,
    post_lines=None,
    pre_tokens=None,
    post_tokens=None,
    strip_dates=False,
    strip_common_prefix_lines=0,
) -> str:
    lines = stdout.split("\n")

    # NOTE: This can cause issues when, for example, reading a CSV with dates in the first column
    if strip_dates:
        # strip iso8601 timestamps
        lines = [
            re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[.]\d{3,9}Z?", "", line)
            for line in lines
        ]
        # strip dates like "2017-08-02 08:48:43 +0000 UTC"
        lines = [
            re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}( [+]\d{4})?( UTC)?", "", line)
            for line in lines
        ]

    # strip common prefixes, useful for things like `gh runs view`
    if strip_common_prefix_lines and len(lines) >= strip_common_prefix_lines:
        prefix = os.path.commonprefix([line.rstrip() for line in lines])
        if prefix:
            lines = [line[len(prefix) :] for line in lines]

    # check that if pre_lines is set, so is post_lines, and vice versa
    assert (pre_lines is None) == (post_lines is None)
    if (
        pre_lines is not None
        and post_lines is not None
        and len(lines) > pre_lines + post_lines
    ):
        lines = (
            lines[:pre_lines]
            + [f"... ({len(lines) - pre_lines - post_lines} truncated) ..."]
            + lines[-post_lines:]
        )

    # check that if pre_tokens is set, so is post_tokens, and vice versa
    assert (pre_tokens is None) == (post_tokens is None)
    if pre_tokens is not None and post_tokens is not None:
        tokenizer = get_tokenizer("gpt-4")  # TODO: use sane default
        tokens = tokenizer.encode(stdout)
        if len(tokens) > pre_tokens + post_tokens:
            lines = (
                [tokenizer.decode(tokens[:pre_tokens])]
                + ["... (truncated output) ..."]
                + [tokenizer.decode(tokens[-post_tokens:])]
            )

    return "\n".join(lines)


def split_commands(script: str) -> list[str]:
    # TODO: write proper tests
    parts = bashlex.parse(script)
    commands = []
    for part in parts:
        if part.kind == "command":
            command_parts = []
            for word in part.parts:
                start, end = word.pos
                command_parts.append(script[start:end])
            command = " ".join(command_parts)
            commands.append(command)
        elif part.kind == "compound":
            for node in part.list:
                command_parts = []
                for word in node.parts:
                    start, end = word.pos
                    command_parts.append(script[start:end])
                command = " ".join(command_parts)
                commands.append(command)
        elif part.kind in ["function", "pipeline", "list"]:
            commands.append(script[part.pos[0] : part.pos[1]])
        else:
            logger.warning(
                f"Unknown shell script part of kind '{part.kind}', hoping this works"
            )
            commands.append(script[part.pos[0] : part.pos[1]])
    return commands


tool = ToolSpec(
    name="shell",
    desc="Executes shell commands.",
    instructions=instructions,
    instructions_format=instructions_format,
    examples=examples,
    execute=execute_shell,
    block_types=["shell"],
    parameters=[
        Parameter(
            name="command",
            type="string",
            description="The shell command with arguments to execute.",
            required=True,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
