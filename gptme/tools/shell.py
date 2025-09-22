"""
The assistant can execute shell commands with bash by outputting code blocks with `shell` as the language.
"""

import atexit
import logging
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import time
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

# ANSI escape sequence pattern for stripping terminal formatting
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi_codes(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


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

# Commands that should be denied without user confirmation due to their dangerous nature
# Define deny groups with shared reasons
deny_groups = [
    (
        [
            r"git\s+add\s+\.",
            r"git\s+add\s+-A",
            r"git\s+add\s+--all",
            r"git\s+commit\s+-a",
            r"git\s+commit\s+--all",
        ],
        "Instead of bulk git operations, use selective commands: `git add <specific-files>` to stage only intended files, then `git commit`.",
    ),
    (
        [
            r"rm\s+-rf\s+/",
            r"sudo\s+rm\s+-rf\s+/",
            r"rm\s+-rf\s+\*",
        ],
        "Destructive file operations are blocked. Specify exact paths and avoid operations that could delete system files or entire directories.",
    ),
    (
        [
            r"chmod\s+-R\s+777",
            r"chmod\s+777",
        ],
        "Overly permissive chmod operations are blocked. Use safer permissions like `chmod 755` or `chmod 644` and be specific about target files.",
    ),
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

Shell commands can be configured to timeout by setting the GPTME_SHELL_TIMEOUT environment variable.
- Set GPTME_SHELL_TIMEOUT=30 for a 30-second timeout
- Set GPTME_SHELL_TIMEOUT=0 to disable timeout
- Invalid values default to 60 seconds
- If not set, commands run without timeout

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

    def run(
        self, code: str, output=True, timeout: float | None = None
    ) -> tuple[int | None, str, str]:
        """Runs a command in the shell and returns the output."""
        commands = split_commands(code)
        res_code: int | None = None
        res_stdout, res_stderr = "", ""
        for cmd in commands:
            res_cur = self._run(cmd, output=output, timeout=timeout)
            res_code = res_cur[0]
            res_stdout += res_cur[1]
            res_stderr += res_cur[2]
            if res_code != 0:
                return res_code, res_stdout, res_stderr
        return res_code, res_stdout, res_stderr

    def _run(
        self, command: str, output=True, tries=0, timeout: float | None = None
    ) -> tuple[int | None, str, str]:
        assert self.process.stdin

        # run the command, redirect stdin to /dev/null to prevent commands from
        # inheriting bash's pipe stdin (which causes issues with nested gptme calls)
        # only use this for commands that don't already redirect stdin, like << EOF
        try:
            command_parts = list(
                shlex.shlex(command, posix=True, punctuation_chars=True)
            )
            if (
                "<" not in command_parts
                and "<<" not in command_parts
                and "<<<" not in command_parts
                and "|" not in command_parts
            ):
                command += " < /dev/null"
        except ValueError as e:
            logger.warning("Failed shlex parsing command, using raw command", e)

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
                return self._run(
                    command, output=output, tries=tries + 1, timeout=timeout
                )
            else:
                raise

        self.process.stdin.flush()

        stdout: list[str] = []
        stderr: list[str] = []
        return_code: int | None = None
        start_time = time.time() if timeout else None

        try:
            while True:
                # Calculate remaining timeout
                select_timeout = None
                if timeout and start_time:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        # Timeout exceeded
                        logger.info(f"Command timed out after {timeout} seconds")
                        # Terminate the command gracefully
                        try:
                            self.process.send_signal(signal.SIGTERM)
                            time.sleep(0.1)  # Give it a moment to terminate
                            if self.process.poll() is None:
                                self.process.kill()
                        except Exception as e:
                            logger.warning(f"Error terminating timed-out process: {e}")

                        partial_stdout = "".join(stdout).strip()
                        partial_stderr = "".join(stderr).strip()
                        return (
                            -124,
                            partial_stdout,
                            partial_stderr,
                        )  # Use timeout exit code (124)

                    select_timeout = min(
                        1.0, timeout - elapsed
                    )  # Check at least every second

                rlist, _, _ = select.select(
                    [self.stdout_fd, self.stderr_fd], [], [], select_timeout
                )

                # Handle timeout in select
                if not rlist and timeout and start_time:
                    continue  # Will be caught by timeout check above

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


def is_denylisted(cmd: str) -> tuple[bool, str | None]:
    """Check if a command contains dangerous patterns that should be denied.

    Returns:
        tuple[bool, str | None]: (is_denied, reason_if_denied)
    """
    cmd_normalized = " ".join(cmd.split())  # normalize whitespace

    # Check deny groups
    for patterns, reason in deny_groups:
        for pattern in patterns:
            if re.search(pattern, cmd_normalized, re.IGNORECASE):
                return True, reason

    return False, None


def _format_shell_output(
    cmd: str,
    stdout: str,
    stderr: str,
    returncode: int | None,
    interrupted: bool,
    allowlisted: bool,
    pwd_changed: bool,
    current_cwd: str,
    timed_out: bool = False,
    timeout_value: float | None = None,
) -> str:
    """Format shell command output into a message."""
    # Strip ANSI escape sequences from output
    stdout = strip_ansi_codes(stdout)
    stderr = strip_ansi_codes(stderr)

    # Apply shortening logic
    stdout = _shorten_stdout(stdout, pre_tokens=2000, post_tokens=8000)
    stderr = _shorten_stdout(stderr, pre_tokens=2000, post_tokens=2000)

    # Format header
    if timed_out:
        header = (
            f"Command timed out (after {timeout_value}s)"
            if timeout_value
            else "Command timed out"
        )
    elif interrupted:
        header = "Command interrupted"
    else:
        header = f"Ran {'allowlisted ' if allowlisted else ''}command"

    msg = _format_block_smart(header, cmd, lang="bash") + "\n\n"

    # Add output
    if stdout:
        msg += _format_block_smart("", stdout, "stdout").lstrip() + "\n\n"
    if stderr:
        msg += _format_block_smart("", stderr, "stderr").lstrip() + "\n\n"
    if not stdout and not stderr:
        if timed_out:
            msg += "No output before timeout\n"
        elif interrupted:
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
    cmd: str, _: Path | None, confirm: ConfirmFunc, timeout: float | None = None
) -> Generator[Message, None, None]:
    """Execute shell command and format output."""
    shell = get_shell()
    allowlisted = is_allowlisted(cmd)
    prev_cwd = os.getcwd()

    try:
        returncode, stdout, stderr = shell.run(cmd, timeout=timeout)
        interrupted = False
        timed_out = returncode == -124  # Our timeout return code
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
        timed_out = False
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
        timed_out,
        timeout_value=timeout,
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

    # Check for timeout from environment variable
    timeout = None
    timeout_env = os.environ.get("GPTME_SHELL_TIMEOUT")
    if timeout_env is not None:
        try:
            timeout = float(timeout_env) if timeout_env else 60.0
            if timeout <= 0:
                timeout = None  # Disable timeout if set to 0 or negative
        except ValueError:
            logger.warning(
                f"Invalid GPTME_SHELL_TIMEOUT value: {timeout_env}, using default 60s"
            )
            timeout = 60.0

    # Check if command is denylisted - these are blocked entirely
    is_denied, deny_reason = is_denylisted(cmd)
    if is_denied:
        yield Message("system", f"Command denied: `{cmd}`\n\n{deny_reason}")
        return

    # Skip confirmation for allowlisted commands
    if is_allowlisted(cmd):
        yield from execute_shell_impl(cmd, None, lambda _: True, timeout=timeout)
    else:
        # Create a wrapper function that passes timeout to execute_shell_impl
        def execute_fn(
            cmd: str, path: Path | None, confirm: ConfirmFunc
        ) -> Generator[Message, None, None]:
            return execute_shell_impl(cmd, path, confirm, timeout=timeout)

        yield from execute_with_confirmation(
            cmd,
            args,
            kwargs,
            confirm,
            execute_fn=execute_fn,
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

    # Preprocess script to handle quoted heredoc delimiters that bashlex can't parse
    processed_script = _preprocess_quoted_heredocs(script)

    parts = bashlex.parse(processed_script)
    commands = []
    for part in parts:
        if part.kind == "command":
            command_parts = []
            for word in part.parts:
                start, end = word.pos
                command_parts.append(processed_script[start:end])
            command = " ".join(command_parts)
            commands.append(command)
        elif part.kind == "compound":
            for node in part.list:
                command_parts = []
                for word in node.parts:
                    start, end = word.pos
                    command_parts.append(processed_script[start:end])
                command = " ".join(command_parts)
                commands.append(command)
        elif part.kind in ["function", "pipeline", "list"]:
            commands.append(processed_script[part.pos[0] : part.pos[1]])
        else:
            logger.warning(
                f"Unknown shell script part of kind '{part.kind}', hoping this works"
            )
            commands.append(processed_script[part.pos[0] : part.pos[1]])

    # Convert back to original heredoc syntax if we modified it
    return [_restore_quoted_heredocs(cmd, script) for cmd in commands]


def _preprocess_quoted_heredocs(script: str) -> str:
    """Convert quoted heredoc delimiters to unquoted ones for bashlex parsing."""
    # Match heredoc operators with quoted delimiters: <<'DELIMITER' or <<"DELIMITER"
    # Allow optional whitespace between << and the quoted delimiter
    heredoc_pattern = re.compile(r'<<\s*(["\'])([^"\'\s]+)\1')
    return heredoc_pattern.sub(r"<<\2", script)


def _restore_quoted_heredocs(command: str, original_script: str) -> str:
    """Restore quoted heredoc delimiters in the processed command."""
    # If the original script had quoted heredocs, restore them
    # Allow optional whitespace between << and the quoted delimiter
    heredoc_pattern = re.compile(r'<<\s*(["\'])([^"\'\s]+)\1')
    original_matches = heredoc_pattern.findall(original_script)

    if not original_matches:
        return command

    # Replace unquoted delimiters back to quoted ones
    for quote, delimiter in original_matches:
        unquoted_pattern = f"<<{delimiter}"
        quoted_replacement = f"<<{quote}{delimiter}{quote}"
        command = command.replace(unquoted_pattern, quoted_replacement)

    return command


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
