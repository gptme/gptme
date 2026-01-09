"""
The assistant can execute shell commands with bash by outputting code blocks with `shell` as the language.

Configuration:
    GPTME_SHELL_TIMEOUT: Environment variable to configure command timeout (set before starting gptme)
        - Set to a number (e.g., 30) for timeout in seconds
        - Set to 0 to disable timeout
        - Invalid values default to 1200 seconds (20 minutes)
        - If not set, defaults to 1200 seconds (20 minutes)
"""

import atexit
import logging
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import bashlex

from ..message import Message
from ..util import get_installed_programs
from ..util.ask_execute import execute_with_confirmation
from ..util.output_storage import save_large_output
from ..util.tokens import get_tokenizer
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


# Background job management for long-running commands (Issue #576)
# Maximum buffer size to prevent memory issues (1MB per buffer)
_MAX_BUFFER_SIZE = 1024 * 1024


@dataclass
class BackgroundJob:
    """Tracks a background process with its output."""

    id: int
    command: str
    process: subprocess.Popen
    start_time: float
    stdout_buffer: list[str] = field(default_factory=list)
    stderr_buffer: list[str] = field(default_factory=list)
    _reader_thread: threading.Thread | None = field(default=None, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _buffer_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def start_reader(self) -> None:
        """Start background thread to read output."""
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()

    def _read_output(self) -> None:
        """Read stdout/stderr in background thread."""
        stdout_fd = self.process.stdout.fileno() if self.process.stdout else -1
        stderr_fd = self.process.stderr.fileno() if self.process.stderr else -1
        fds = [fd for fd in [stdout_fd, stderr_fd] if fd >= 0]

        while not self._stop_event.is_set() and self.process.poll() is None:
            try:
                readable, _, _ = select.select(fds, [], [], 0.1)
                for fd in readable:
                    data = os.read(fd, 4096).decode("utf-8", errors="replace")
                    if data:
                        with self._buffer_lock:
                            if fd == stdout_fd:
                                self._append_to_buffer(self.stdout_buffer, data)
                            else:
                                self._append_to_buffer(self.stderr_buffer, data)
            except (OSError, ValueError):
                break

        # Final read after process exits
        if self.process.stdout:
            try:
                remaining = self.process.stdout.read()
                if remaining:
                    with self._buffer_lock:
                        self._append_to_buffer(self.stdout_buffer, remaining)
            except (OSError, ValueError):
                pass
        if self.process.stderr:
            try:
                remaining = self.process.stderr.read()
                if remaining:
                    with self._buffer_lock:
                        self._append_to_buffer(self.stderr_buffer, remaining)
            except (OSError, ValueError):
                pass

    def _append_to_buffer(self, buffer: list[str], data: str) -> None:
        """Append data to buffer, enforcing size limit."""
        buffer.append(data)
        # Check total size and truncate from front if needed
        total_size = sum(len(s) for s in buffer)
        while total_size > _MAX_BUFFER_SIZE and len(buffer) > 1:
            removed = buffer.pop(0)
            total_size -= len(removed)

    def get_output(self) -> tuple[str, str]:
        """Get accumulated stdout and stderr."""
        with self._buffer_lock:
            return "".join(self.stdout_buffer), "".join(self.stderr_buffer)

    def is_running(self) -> bool:
        """Check if process is still running."""
        return self.process.poll() is None

    def elapsed_time(self) -> float:
        """Get elapsed time in seconds."""
        return time.time() - self.start_time

    def kill(self) -> None:
        """Terminate the background job."""
        self._stop_event.set()
        try:
            self.process.terminate()
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        # Join reader thread to ensure clean shutdown
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)


# Global storage for background jobs
_background_jobs: dict[int, BackgroundJob] = {}
_next_job_id: int = 1
_job_lock: threading.Lock = threading.Lock()


def _get_next_job_id() -> int:
    """Get next available job ID (thread-safe)."""
    global _next_job_id
    with _job_lock:
        job_id = _next_job_id
        _next_job_id += 1
        return job_id


def start_background_job(command: str) -> BackgroundJob:
    """Start a command as a background job (thread-safe)."""
    # Proactively clean up finished jobs to prevent memory accumulation
    cleanup_finished_jobs()

    job_id = _get_next_job_id()

    # Start process with separate stdout/stderr pipes
    process = subprocess.Popen(
        ["bash", "-c", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    job = BackgroundJob(
        id=job_id,
        command=command,
        process=process,
        start_time=time.time(),
    )
    job.start_reader()

    with _job_lock:
        _background_jobs[job_id] = job

    return job


def get_background_job(job_id: int) -> BackgroundJob | None:
    """Get a background job by ID."""
    with _job_lock:
        return _background_jobs.get(job_id)


def list_background_jobs() -> list[BackgroundJob]:
    """List all background jobs, cleaning up finished ones first."""
    cleanup_finished_jobs()
    with _job_lock:
        return list(_background_jobs.values())


def cleanup_finished_jobs() -> None:
    """Remove finished jobs from tracking (thread-safe)."""
    with _job_lock:
        finished = [
            job_id for job_id, job in _background_jobs.items() if not job.is_running()
        ]
        for job_id in finished:
            del _background_jobs[job_id]


def reset_background_jobs() -> None:
    """Stop and clean up all background jobs. Called on exit and for testing."""
    global _next_job_id
    with _job_lock:
        # Kill any running jobs
        for job in _background_jobs.values():
            if job.is_running():
                job.kill()
        _background_jobs.clear()
        _next_job_id = 1


# Register cleanup handler to prevent orphaned bg jobs when gptme exits (Issue #993)
atexit.register(reset_background_jobs)


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
            r"git\s+add\s+\.(?:\s|$)",  # Match 'git add .' but not '.gitignore'
            r"git\s+add\s+-A",
            r"git\s+add\s+--all",
            r"git\s+commit\s+-a",
            r"git\s+commit\s+--all",
        ],
        "Instead of bulk git operations, use selective commands: `git add <specific-files>` to stage only intended files, then `git commit`.",
    ),
    (
        [
            r"git\s+reset\s+--hard",
            r"git\s+clean\s+-[fFdDxX]+",
            r"git\s+push\s+(-f|--force)(?!-)",  # Allow --force-with-lease
            r"git\s+reflog\s+expire",
            r"git\s+filter-branch",
        ],
        "Destructive git operations are blocked. Use safer alternatives: `git stash` to save changes, `git reset --soft` to uncommit without losing changes, `git push --force-with-lease` for safer force pushes.",
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
    (
        [
            r"pkill\s",
            r"killall\s",
        ],
        "Killing processes indiscriminately is blocked. Use `ps aux | grep <process-name>` to find specific PIDs and `kill <PID>` to terminate them safely.",
    ),
    (
        [
            # Pipe to shell interpreters (bash, sh, and their variants with paths)
            r"\|\s*(bash|sh|/bin/bash|/bin/sh)(?:\s|$)",
            # Pipe to script interpreters
            r"\|\s*(python|python3|perl|ruby|node)(?:\s|$)",
        ],
        "Piping to shell interpreters or script execution is blocked. This pattern can execute arbitrary code and is a security risk.",
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
    "rg",
    "ag",
    "ast-grep",
    "hyperfine",
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

## Background Jobs

For long-running commands (dev servers, builds, etc.), use background jobs:
- `bg <command>` - Start command in background, returns job ID
- `jobs` - List all background jobs with status
- `output <id>` - Show accumulated output from a job
- `kill <id>` - Terminate a background job

This prevents blocking on commands like `npm run dev` that run indefinitely.
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

#### Background jobs for long-running commands

> User: start a dev server
> Assistant: I'll start the dev server as a background job so it doesn't block:
{ToolUse("shell", [], "bg npm run dev").to_output(tool_format)}
> System: Started background job **#1**: `npm run dev`
>
> Use these commands to manage it:
> - `jobs` - List all background jobs
> - `output 1` - Show output from job #1
> - `kill 1` - Terminate job #1

> User: check the server output
> Assistant: Let me check the output from the background job:
{ToolUse("shell", [], "output 1").to_output(tool_format)}
> System: **Job #1** - Running (45.2s)
> Command: `npm run dev`
>
> ```stdout
> > project@1.0.0 dev
> > vite
>
> VITE v5.0.0  ready in 300 ms
>
> ➜  Local:   http://localhost:5173/
> ```

> User: stop the server
> Assistant: I'll terminate the background job:
{ToolUse("shell", [], "kill 1").to_output(tool_format)}
> System: Terminated job #1: `npm run dev`
""".strip()


class ShellSession:
    process: subprocess.Popen
    stdout_fd: int
    stderr_fd: int
    delimiter: str
    start_marker: str  # Fix for Issue #408: Add start marker to prevent output mixing

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
            start_new_session=True,  # Create new process group for proper signal handling
        )
        self.stdout_fd = self.process.stdout.fileno()  # type: ignore
        self.stderr_fd = self.process.stderr.fileno()  # type: ignore
        self.delimiter = "END_OF_COMMAND_OUTPUT"
        self.start_marker = "START_OF_COMMAND_OUTPUT"

        # set GIT_PAGER=cat
        self.run("export PAGER=")
        self.run("export GH_PAGER=")
        self.run("export GIT_PAGER=cat")
        # prevent editors from opening (they can break terminal state)
        self.run("export EDITOR=true")
        self.run("export GIT_EDITOR=true")
        self.run("export VISUAL=true")
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

        # Diagnostic logging for Issue #408: Log command start
        logger.debug(f"Shell: Running command: {command[:200]}")

        # Redirect stdin to /dev/null to prevent commands from inheriting bash's pipe stdin
        # Use shlex to properly parse commands and respect quotes
        # Only add for commands that don't already redirect stdin
        try:
            command_parts = list(
                shlex.shlex(command, posix=True, punctuation_chars=True)
            )

            # Check if there's already stdin redirection
            has_stdin_redirect = (
                "<" in command_parts or "<<" in command_parts or "<<<" in command_parts
            )

            # For pipelines, redirect stdin for the first command only
            if "|" in command_parts and not has_stdin_redirect:
                # Find first unquoted pipe in original command
                # We can't use shlex.join() because it quotes shell operators like 2>&1
                try:
                    pipe_pos = _find_first_unquoted_pipe(command)
                    if pipe_pos is not None and pipe_pos > 0:
                        first_cmd = command[:pipe_pos].rstrip()
                        rest = command[pipe_pos + 1 :].lstrip()
                        command = f"{first_cmd} < /dev/null | {rest}"
                except Exception as e:
                    # Fallback to raw command if parsing fails
                    logger.warning(f"Failed to parse pipe in command '{command}': {e}")
            elif not has_stdin_redirect and "|" not in command_parts:
                # No pipe and no stdin redirection - add /dev/null
                command += " < /dev/null"
        except ValueError as e:
            logger.warning(f"Failed shlex parsing command, using raw command: {e}")

        # Issue #408: Drain any leftover stderr from previous commands BEFORE sending new command
        # This ensures we don't mix stderr from previous commands with the current one
        while True:
            pre_drain_rlist, _, _ = select.select([self.stderr_fd], [], [], 0.05)
            if not pre_drain_rlist:
                break
            pre_drain_data = os.read(self.stderr_fd, 2**16).decode(
                "utf-8", errors="replace"
            )
            if not pre_drain_data:
                break
            # Discard leftover stderr from previous commands
            if pre_drain_data.strip():
                logger.debug(f"Shell: Pre-command stderr drain: {pre_drain_data[:80]}")

        # Generate unique command ID to prevent output mixing (Issue #408)
        cmd_id = f"{time.time_ns()}"
        start_marker_pattern = f"{self.start_marker}_{cmd_id}"

        full_command = f"echo {start_marker_pattern}\n"  # Start marker first
        full_command += f"{command}\n"
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

        # Issue #408: Track whether we've seen the start marker for this command
        seen_start_marker = False

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
                        # Terminate the entire process group (bash + all child processes)
                        try:
                            pgid = os.getpgid(self.process.pid)
                            os.killpg(pgid, signal.SIGTERM)
                            time.sleep(0.1)  # Give it a moment to terminate
                            if self.process.poll() is None:
                                os.killpg(pgid, signal.SIGKILL)
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
                    data = os.read(fd, 2**16).decode("utf-8", errors="replace")
                    lines = data.splitlines(keepends=True)
                    re_returncode = re.compile(r"ReturnCode:(\d+)")
                    for line in lines:
                        # Issue #408: Skip stdout until we see the start marker
                        # Only apply to stdout - stderr should pass through unfiltered
                        if fd == self.stdout_fd and not seen_start_marker:
                            if start_marker_pattern in line:
                                seen_start_marker = True
                                logger.debug(
                                    f"Shell: Start marker detected: {start_marker_pattern[:50]}"
                                )
                            else:
                                # Discard output before start marker (leftover from previous commands)
                                if line.strip():  # Only log non-empty lines
                                    logger.debug(
                                        f"Shell: Discarding pre-marker output: {line[:80]}"
                                    )
                            continue

                        if "ReturnCode:" in line and self.delimiter in line:
                            # Diagnostic logging for Issue #408: Log delimiter detection
                            logger.debug(
                                f"Shell: Delimiter detected in line: {line.strip()[:200]}"
                            )

                            # Capture last stdout before delimiter to detect unexpected content
                            if stdout:
                                # Get last 3 lines or all if fewer
                                last_lines = stdout[-3:] if len(stdout) >= 3 else stdout
                                last_stdout = "".join(last_lines).strip()[:300]
                                logger.debug(
                                    f"Shell: Last stdout before delimiter: {last_stdout}"
                                )

                            if match := re_returncode.search(line):
                                return_code = int(match.group(1))
                            # if command is cd and successful, we need to change the directory
                            if command.startswith("cd ") and return_code == 0:
                                ex, pwd, _ = self._run("pwd", output=False)
                                assert ex == 0
                                os.chdir(pwd.strip())

                            # Issue #408: Drain any remaining stderr before returning
                            # This prevents stderr from leaking to the next command
                            # Use multiple attempts with longer initial timeout to ensure
                            # stderr has time to arrive from bash
                            drain_empty_count = 0
                            while (
                                drain_empty_count < 2
                            ):  # Require 2 empty reads to be sure
                                drain_rlist, _, _ = select.select(
                                    [self.stderr_fd], [], [], 0.1
                                )
                                if not drain_rlist:
                                    drain_empty_count += 1
                                    continue
                                drain_data = os.read(self.stderr_fd, 2**16).decode(
                                    "utf-8", errors="replace"
                                )
                                if not drain_data:
                                    drain_empty_count += 1
                                    continue
                                # Reset counter when we get data
                                drain_empty_count = 0
                                stderr.append(drain_data)
                                if output:
                                    print(drain_data, end="", file=sys.stderr)
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
        try:
            pgid = os.getpgid(self.process.pid)
            os.killpg(pgid, signal.SIGTERM)
            self.process.wait(timeout=0.2)
            if self.process.poll() is None:
                os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            # Process already exited; this can happen due to cleanup races.
            pass
        except Exception as e:
            logger.warning(f"Error terminating process during close: {e}")

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


def _has_file_redirection(cmd: str) -> bool:
    """Check if command contains file output redirection (> or >>).

    Returns True if the command contains > or >> outside of quoted strings.
    Ignores heredoc operators (<< and <<-).
    """
    quoted_regions = _find_quotes(cmd)

    # Look for > or >> that are not in quotes and not part of heredoc
    i = 0
    while i < len(cmd):
        # Skip if we're in a quoted region
        if _is_in_quoted_region(i, quoted_regions):
            i += 1
            continue

        # Check for >>
        if i < len(cmd) - 1 and cmd[i : i + 2] == ">>":
            return True

        # Check for > but not << (heredoc)
        if cmd[i] == ">":
            # Make sure it's not part of << or <<-
            if i > 0 and cmd[i - 1] == "<":
                i += 1
                continue
            return True

        i += 1

    return False


def is_allowlisted(cmd: str) -> bool:
    # Check if all commands in the pipeline are allowlisted
    for match in cmd_regex.finditer(cmd):
        for group in match.groups():
            if group and group not in allowlist_commands:
                return False

    # Check for file redirections (>, >>)
    # File redirections with allowlisted commands can be used to write malicious content
    # Example: echo "malicious_code" > /tmp/exploit.sh
    if _has_file_redirection(cmd):
        return False

    return True


def _find_quotes(cmd: str) -> list[tuple[int, int]]:
    """Find all quoted regions in a command string.

    Returns a list of (start, end) tuples for each quoted region.
    """
    quoted_regions = []
    in_single = False
    in_double = False
    start = -1

    i = 0
    while i < len(cmd):
        c = cmd[i]

        # Handle escape sequences
        if c == "\\" and i + 1 < len(cmd):
            i += 2
            continue

        # Handle single quotes
        if c == "'" and not in_double:
            if not in_single:
                start = i
                in_single = True
            else:
                quoted_regions.append((start, i + 1))
                in_single = False

        # Handle double quotes
        elif c == '"' and not in_single:
            if not in_double:
                start = i
                in_double = True
            else:
                quoted_regions.append((start, i + 1))
                in_double = False

        i += 1

    return quoted_regions


def _find_heredoc_regions(cmd: str) -> list[tuple[int, int]]:
    """Find all heredoc regions in a command string.

    Heredoc syntax: << DELIMITER or <<- DELIMITER
    The delimiter can be quoted: << 'EOF' or << "EOF"

    Returns a list of (start, end) tuples for each heredoc content region.
    """
    heredoc_regions = []

    # Pattern to match heredoc operators with optional quotes around delimiter
    # Matches: << or <<- followed by optional whitespace and delimiter (with optional quotes)
    heredoc_pattern = re.compile(r"<<-?\s*([\"']?)(\w+)\1")

    for match in heredoc_pattern.finditer(cmd):
        delimiter = match.group(2)

        # Find where the content starts (after the first newline after the marker)
        search_start = match.end()
        newline_idx = cmd.find("\n", search_start)
        if newline_idx == -1:
            continue  # No content

        content_start = newline_idx + 1

        # Find the line with just the delimiter
        pos = content_start
        while True:
            newline_idx = cmd.find("\n", pos)
            if newline_idx == -1:
                # Check if remaining text is the delimiter
                if cmd[pos:].strip() == delimiter:
                    heredoc_regions.append((content_start, pos))
                break

            # Check if the line from pos to newline_idx is just the delimiter
            line = cmd[pos:newline_idx]
            if line.strip() == delimiter:
                heredoc_regions.append((content_start, pos))
                break

            pos = newline_idx + 1

    return heredoc_regions


def _is_in_quoted_region(pos: int, quoted_regions: list[tuple[int, int]]) -> bool:
    """Check if a position is within any quoted region."""
    for start, end in quoted_regions:
        if start <= pos < end:
            return True
    return False


def _find_first_unquoted_pipe(command: str) -> int | None:
    """Find the position of the first pipe operator that's not in quotes.

    Returns None if no unquoted pipe is found.
    Skips logical OR operators (||).
    """
    quoted_regions = _find_quotes(command)

    pos = 0
    while True:
        pipe_pos = command.find("|", pos)
        if pipe_pos == -1:
            return None

        # Check if this pipe is inside quotes
        if not _is_in_quoted_region(pipe_pos, quoted_regions):
            # Check if this is part of || (logical OR)
            if pipe_pos + 1 < len(command) and command[pipe_pos + 1] == "|":
                # Skip the || operator
                pos = pipe_pos + 2
                continue

            return pipe_pos

        # Try next pipe
        pos = pipe_pos + 1


def is_denylisted(cmd: str) -> tuple[bool, str | None, str | None]:
    """Check if a command contains dangerous patterns that should be denied.

    Only checks actual commands, not content in quoted strings or heredocs.

    Returns:
        tuple[bool, str | None, str | None]: (is_denied, reason_if_denied, matched_command)
    """
    # Find both quoted regions and heredoc regions in the original command
    # (heredocs require newlines to be detected properly)
    quoted_regions = _find_quotes(cmd)
    heredoc_regions = _find_heredoc_regions(cmd)

    # Combine all safe regions
    safe_regions = quoted_regions + heredoc_regions

    # Check deny groups against the original command
    # We don't normalize because it would break heredoc detection
    for patterns, reason in deny_groups:
        for pattern in patterns:
            match = re.search(pattern, cmd, re.IGNORECASE)
            if match:
                # Check if the match is within a safe region (quoted or heredoc)
                match_start = match.start()
                if not _is_in_quoted_region(match_start, safe_regions):
                    # Return the matched text to show in error message
                    return True, reason, match.group(0)

    return False, None, None


def _format_shell_output(
    cmd: str,
    stdout: str,
    stderr: str,
    returncode: int | None,
    interrupted: bool,
    allowlisted: bool,
    timed_out: bool = False,
    timeout_value: float | None = None,
    logdir: Path | None = None,
) -> str:
    """Format shell command output into a message."""
    # Strip ANSI escape sequences from output
    stdout = strip_ansi_codes(stdout)
    stderr = strip_ansi_codes(stderr)

    # Apply shortening logic with output storage
    stdout = _shorten_stdout(
        stdout, pre_tokens=2000, post_tokens=8000, logdir=logdir, cmd=cmd
    )
    stderr = _shorten_stdout(
        stderr, pre_tokens=2000, post_tokens=2000, logdir=logdir, cmd=f"{cmd} (stderr)"
    )

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

    # Truncate long commands to reduce context waste (Issue #974)
    # The full command is already visible in the assistant's code block
    if len(cmd) > 100 or cmd.count("\n") > 2:
        first_line = cmd.split("\n")[0][:80]
        line_count = cmd.count("\n") + 1
        cmd_display = (
            f"{first_line}... ({line_count} {'line' if line_count == 1 else 'lines'})"
        )
    else:
        cmd_display = cmd

    msg = _format_block_smart(header, cmd_display, lang="bash") + "\n\n"

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

    return msg


# Background command handlers
def execute_bg_command(command: str) -> Generator[Message, None, None]:
    """Start a command as a background job."""
    if not command.strip():
        yield Message("system", "Usage: `bg <command>`\n\nExample: `bg npm run dev`")
        return

    # Check if command is denylisted - blocked even for background jobs
    is_denied, deny_reason, matched_cmd = is_denylisted(command)
    if is_denied:
        yield Message("system", f"Background command denied: `{matched_cmd}`\n\n{deny_reason}")
        return

    job = start_background_job(command)
    yield Message(
        "system",
        f"Started background job **#{job.id}**: `{command}`\n\n"
        f"Use these commands to manage it:\n"
        f"- `jobs` - List all background jobs\n"
        f"- `output {job.id}` - Show output from job #{job.id}\n"
        f"- `kill {job.id}` - Terminate job #{job.id}",
    )


def execute_jobs_command() -> Generator[Message, None, None]:
    """List all background jobs."""
    jobs = list_background_jobs()
    if not jobs:
        yield Message("system", "No background jobs running.")
        return

    lines = ["**Background Jobs:**\n"]
    for job in jobs:
        status = "🟢 Running" if job.is_running() else "⚫ Finished"
        elapsed = job.elapsed_time()
        if elapsed < 60:
            time_str = f"{elapsed:.1f}s"
        else:
            time_str = f"{elapsed / 60:.1f}m"
        lines.append(
            f"- **#{job.id}** [{status}] ({time_str}): `{job.command[:50]}{'...' if len(job.command) > 50 else ''}`"
        )

    yield Message("system", "\n".join(lines))


def execute_output_command(job_id_str: str) -> Generator[Message, None, None]:
    """Show output from a background job."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        yield Message(
            "system", f"Invalid job ID: `{job_id_str}`. Use `jobs` to list active jobs."
        )
        return

    job = get_background_job(job_id)
    if not job:
        yield Message(
            "system", f"No job with ID #{job_id}. Use `jobs` to list active jobs."
        )
        return

    stdout, stderr = job.get_output()
    status = (
        "Running"
        if job.is_running()
        else f"Finished (exit code: {job.process.returncode})"
    )
    elapsed = job.elapsed_time()

    msg = f"**Job #{job_id}** - {status} ({elapsed:.1f}s)\n"
    msg += f"Command: `{job.command}`\n\n"

    if stdout:
        # Truncate if too long
        if len(stdout) > 8000:
            stdout = stdout[-8000:]
            msg += "```stdout\n...(truncated)...\n" + stdout + "\n```\n\n"
        else:
            msg += f"```stdout\n{stdout}\n```\n\n"
    if stderr:
        if len(stderr) > 2000:
            stderr = stderr[-2000:]
            msg += "```stderr\n...(truncated)...\n" + stderr + "\n```\n\n"
        else:
            msg += f"```stderr\n{stderr}\n```\n\n"
    if not stdout and not stderr:
        msg += "No output yet.\n"

    yield Message("system", msg)


def execute_kill_command(job_id_str: str) -> Generator[Message, None, None]:
    """Terminate a background job."""
    try:
        job_id = int(job_id_str)
    except ValueError:
        yield Message(
            "system", f"Invalid job ID: `{job_id_str}`. Use `jobs` to list active jobs."
        )
        return

    job = get_background_job(job_id)
    if not job:
        yield Message(
            "system", f"No job with ID #{job_id}. Use `jobs` to list active jobs."
        )
        return

    if not job.is_running():
        yield Message(
            "system",
            f"Job #{job_id} is already finished (exit code: {job.process.returncode}).",
        )
        return

    job.kill()
    yield Message("system", f"Terminated job #{job_id}: `{job.command}`")


def execute_shell_impl(
    cmd: str, logdir: Path | None, confirm: ConfirmFunc, timeout: float | None = None
) -> Generator[Message, None, None]:
    """Execute shell command and format output."""
    shell = get_shell()
    allowlisted = is_allowlisted(cmd)

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
            pgid = os.getpgid(shell.process.pid)
            os.killpg(pgid, signal.SIGINT)
            shell.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            logger.info("Process didn't exit gracefully, terminating")
            pgid = os.getpgid(shell.process.pid)
            os.killpg(pgid, signal.SIGTERM)
        except Exception as e:
            logger.warning(f"Error terminating interrupted process: {e}")

        returncode = shell.process.returncode
        interrupted = True
        timed_out = False
    except Exception as e:
        raise ValueError(f"Shell error: {e}") from None

    # Format and yield output
    msg = _format_shell_output(
        cmd,
        stdout,
        stderr,
        returncode,
        interrupted,
        allowlisted,
        timed_out,
        timeout_value=timeout,
        logdir=logdir,
    )
    yield Message("system", msg)

    if interrupted:
        raise KeyboardInterrupt() from None


def get_path_fn(*args, **kwargs) -> Path | None:
    return None


def check_with_shellcheck(cmd: str) -> tuple[bool, bool, str]:
    """
    Run shellcheck on command if available.

    Returns: Tuple of (has_issues: bool, should_block: bool, message: str)
    - has_issues: True if any shellcheck issues found
    - should_block: True if critical error codes found that should prevent execution
    - message: Description of issues found

    Note:
        - Requires shellcheck (sudo apt install shellcheck)
        - Can be disabled with GPTME_SHELLCHECK=off
        - Non-blocking if shellcheck unavailable
        - SC2164 (cd error handling) excluded by default
        - Custom excludes via GPTME_SHELLCHECK_EXCLUDE (comma-separated codes)
        - Error codes via GPTME_SHELLCHECK_ERROR_CODES (comma-separated, default: SC2006)
        - Error codes block execution, other codes show warnings only
    """
    # Check if disabled via environment variable
    if os.environ.get("GPTME_SHELLCHECK", "").lower() in ("off", "false", "0"):
        return False, False, ""

    # Check if shellcheck is available
    if not shutil.which("shellcheck"):
        return False, False, ""

    # Default excluded codes
    # SC2002: Useless cat. Consider 'cmd < file | ..' or 'cmd file | ..' instead
    # SC2016: Expressions don't expand in single quotes, use double quotes for that.
    # SC2164: Use 'cd ... || exit' in case cd fails (too noisy for interactive commands)
    default_excludes = ["SC2002", "SC2016", "SC2164"]

    # Get custom excludes from environment variable
    custom_excludes = os.environ.get("GPTME_SHELLCHECK_EXCLUDE", "").split(",")
    custom_excludes = [code.strip() for code in custom_excludes if code.strip()]

    # Combine default and custom excludes
    all_excludes = default_excludes + custom_excludes
    exclude_str = ",".join(all_excludes)

    # Default error codes (should block execution)
    # SC1011: This apostrophe terminated the single quoted string!
    # SC1073: Couldn't parse this single quoted string. Fix to allow more checks.
    # SC2006: Use $(...) notation instead of legacy backticks (causes formatting issues in commits/PRs)
    default_error_codes = ["SC1011", "SC1073", "SC2006"]

    # Get custom error codes from environment variable
    custom_error_codes_str = os.environ.get("GPTME_SHELLCHECK_ERROR_CODES", "")
    if custom_error_codes_str:
        custom_error_codes = [
            code.strip() for code in custom_error_codes_str.split(",") if code.strip()
        ]
        error_codes = list(set(default_error_codes + custom_error_codes))
    else:
        error_codes = default_error_codes

    # Write command to temp file

    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write("#!/bin/bash\n")
        f.write(cmd)
        temp_path = f.name

    try:
        shellcheck_cmd = ["shellcheck", "-f", "gcc"]
        if exclude_str:
            shellcheck_cmd.extend(["--exclude", exclude_str])
        shellcheck_cmd.append(temp_path)

        result = subprocess.run(
            shellcheck_cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0 and result.stdout:
            output = result.stdout.replace(temp_path, "<command>")

            # Extract error codes from shellcheck output

            triggered_codes = set()
            for line in output.splitlines():
                # Match shellcheck error codes (e.g., SC2006, SC2086)
                match = re.search(r"\[SC\d+\]", line)
                if match:
                    # Extract just the code (e.g., "SC2006")
                    code = match.group().strip("[]")
                    triggered_codes.add(code)

            # Check if any triggered codes are error codes (should block)
            blocking_codes = triggered_codes.intersection(set(error_codes))

            if blocking_codes:
                # Critical issues that should block execution
                codes_str = ", ".join(sorted(blocking_codes))
                message = f"Shellcheck found critical issues that prevent execution:\n```\n{output}```\n\nBlocking codes: {codes_str}"
                return True, True, message
            else:
                # Non-critical warnings
                message = f"Shellcheck found potential issues:\n```\n{output}```"
                return True, False, message

        return False, False, ""
    except (subprocess.TimeoutExpired, Exception):
        return False, False, ""
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass


def _execute_preceding_commands(
    cmds: str, confirm: ConfirmFunc
) -> Generator[Message, None, None]:
    """Execute commands that precede a bg command.

    These commands modify shell state (e.g., cd) that the bg command needs.
    We execute them through the shell session to maintain state.

    Issue #992: Enables patterns like:
        cd /project
        bg npm run dev
    """
    # Use the stateful shell session to execute preceding commands
    # so that state changes (like cd) persist for the bg command
    shell_session = get_shell()

    try:
        # Run the preceding commands to update shell state
        returncode, stdout, stderr = shell_session.run(cmds, timeout=30.0)

        # Only report output if there is any
        output_parts = []
        if stdout and stdout.strip():
            output_parts.append(f"```stdout\n{stdout.strip()}\n```")
        if stderr and stderr.strip():
            output_parts.append(f"```stderr\n{stderr.strip()}\n```")

        if output_parts:
            yield Message(
                "system",
                "Ran preceding commands:\n" + "\n".join(output_parts),
            )

        if returncode != 0:
            yield Message(
                "system",
                f"Warning: Preceding commands exited with code {returncode}",
            )
    except Exception as e:
        yield Message("system", f"Error running preceding commands: {e}")


def execute_shell(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Executes a shell command and returns the output."""
    cmd = get_shell_command(code, args, kwargs)

    # Handle background job commands (Issue #576, #992)
    cmd_stripped = cmd.strip()
    cmd_lower = cmd_stripped.lower()
    cmd_parts = cmd_stripped.split(maxsplit=1)

    # Check for bg command - can be on any line (Issue #992)
    # Split into lines and find if any line starts with "bg "
    lines = cmd_stripped.split("\n")
    bg_line_idx = None
    for i, line in enumerate(lines):
        line_stripped = line.strip().lower()
        if line_stripped.startswith("bg "):
            bg_line_idx = i
            break

    if bg_line_idx is not None:
        # Found a bg command
        if bg_line_idx == 0 and len(lines) == 1:
            # Simple case: bg is the only command
            bg_cmd = cmd_stripped[3:].strip()
            yield from execute_bg_command(bg_cmd)
            return
        elif bg_line_idx > 0:
            # bg is on a later line - execute preceding commands first (Issue #992)
            preceding_cmds = "\n".join(lines[:bg_line_idx])
            if preceding_cmds.strip():
                # Execute preceding commands (they modify shell state like cd)
                yield from _execute_preceding_commands(preceding_cmds, confirm)
            # Now execute the bg command
            bg_line = lines[bg_line_idx].strip()
            bg_cmd = bg_line[3:].strip()  # Remove "bg " prefix
            yield from execute_bg_command(bg_cmd)
            # Execute any remaining commands after bg (unlikely but handle it)
            if bg_line_idx < len(lines) - 1:
                remaining_cmds = "\n".join(lines[bg_line_idx + 1 :])
                if remaining_cmds.strip():
                    yield from execute_shell(remaining_cmds, None, None, confirm)
            return
        else:
            # bg is first line but there are more lines after it
            # Start bg job, then execute remaining commands
            bg_line = lines[0].strip()
            bg_cmd = bg_line[3:].strip()
            yield from execute_bg_command(bg_cmd)
            remaining_cmds = "\n".join(lines[1:])
            if remaining_cmds.strip():
                yield from execute_shell(remaining_cmds, None, None, confirm)
            return

    if cmd_lower == "jobs":
        # List background jobs
        yield from execute_jobs_command()
        return

    if cmd_lower.startswith("output "):
        # Show output from job: output <id>
        job_id_str = cmd_parts[1] if len(cmd_parts) > 1 else ""
        yield from execute_output_command(job_id_str)
        return

    if cmd_lower.startswith("kill ") and len(cmd_parts) == 2:
        # Check if this looks like a job kill (just a number)
        # vs a regular kill command (e.g., kill -9 1234)
        potential_job_id = cmd_parts[1]
        if potential_job_id.isdigit() and get_background_job(int(potential_job_id)):
            yield from execute_kill_command(potential_job_id)
            return
        # Fall through to regular shell execution for other kill commands

    # Check for timeout from environment variable
    # Default to 20 minutes (1200s) if not set
    timeout: float | None = 1200.0
    timeout_env = os.environ.get("GPTME_SHELL_TIMEOUT")
    if timeout_env is not None:
        try:
            timeout = float(timeout_env)
            if timeout <= 0:
                timeout = None  # Disable timeout if set to 0 or negative
        except ValueError:
            logger.warning(
                f"Invalid GPTME_SHELL_TIMEOUT value: {timeout_env}, using default 1200s (20 minutes)"
            )
            timeout = 1200.0

    # Check with shellcheck if available
    has_issues, should_block, shellcheck_msg = check_with_shellcheck(cmd)
    if has_issues:
        yield Message("system", shellcheck_msg)
        # Block execution if critical shellcheck errors found
        if should_block:
            return

    # Check if command is denylisted - these are blocked entirely
    is_denied, deny_reason, matched_cmd = is_denylisted(cmd)
    if is_denied:
        yield Message("system", f"Command denied: `{matched_cmd}`\n\n{deny_reason}")
        return

    # Skip confirmation for allowlisted commands
    if is_allowlisted(cmd):
        logdir = get_path_fn()
        yield from execute_shell_impl(cmd, logdir, lambda _: True, timeout=timeout)
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
    logdir: Path | None = None,
    cmd: str | None = None,
) -> str:
    lines = stdout.split("\n")

    # Save full output before truncation if it will be truncated
    will_truncate_by_lines = (
        pre_lines is not None
        and post_lines is not None
        and len(lines) > pre_lines + post_lines
    )
    will_truncate_by_tokens = False
    if pre_tokens is not None and post_tokens is not None:
        tokenizer = get_tokenizer("gpt-4")
        tokens = tokenizer.encode(stdout)
        will_truncate_by_tokens = len(tokens) > pre_tokens + post_tokens

    # If truncation will happen, save full output to file
    saved_path = None
    if (will_truncate_by_lines or will_truncate_by_tokens) and logdir:
        command_info = f"Command: {cmd}" if cmd else None
        original_tokens = len(tokens) if will_truncate_by_tokens else None
        _, saved_path = save_large_output(
            content=stdout,
            logdir=logdir,
            output_type="shell",
            command_info=command_info,
            original_tokens=original_tokens,
        )

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
    # Skip line truncation if token truncation will happen (token truncation is more precise)
    if (
        pre_lines is not None
        and post_lines is not None
        and len(lines) > pre_lines + post_lines
        and not will_truncate_by_tokens
    ):
        truncation_msg = f"... ({len(lines) - pre_lines - post_lines} lines truncated"
        if saved_path:
            truncation_msg += f", full output saved to {saved_path}"
        truncation_msg += ") ..."
        lines = lines[:pre_lines] + [truncation_msg] + lines[-post_lines:]

    # check that if pre_tokens is set, so is post_tokens, and vice versa
    assert (pre_tokens is None) == (post_tokens is None)
    if pre_tokens is not None and post_tokens is not None:
        if not will_truncate_by_tokens:
            tokenizer = get_tokenizer("gpt-4")  # TODO: use sane default
            tokens = tokenizer.encode(stdout)
        if len(tokens) > pre_tokens + post_tokens:
            truncation_msg = "... (output truncated"
            if saved_path:
                truncation_msg += f", full output saved to {saved_path}"
            truncation_msg += ") ..."
            lines = (
                [tokenizer.decode(tokens[:pre_tokens])]
                + [truncation_msg]
                + [tokenizer.decode(tokens[-post_tokens:])]
            )

    return "\n".join(lines)


def _find_max_heredoc_pos(node, current_max: int = 0) -> int:
    """Recursively find the maximum position from any heredoc nodes.

    This is needed because bashlex stores heredoc content in nested RedirectNode
    objects, and the top-level part.pos doesn't include the heredoc content positions.
    """
    max_pos = current_max

    # Check if this node has a heredoc
    if hasattr(node, "heredoc") and node.heredoc:
        heredoc_end = node.heredoc.pos[1]
        max_pos = max(max_pos, heredoc_end)

    # Recursively check child nodes
    if hasattr(node, "parts"):
        for part in node.parts:
            max_pos = max(max_pos, _find_max_heredoc_pos(part, max_pos))

    if hasattr(node, "list"):
        for item in node.list:
            max_pos = max(max_pos, _find_max_heredoc_pos(item, max_pos))

    return max_pos


def split_commands(script: str) -> list[str]:
    # TODO: write proper tests

    # Preprocess script to handle quoted heredoc delimiters that bashlex can't parse
    processed_script = _preprocess_quoted_heredocs(script)

    try:
        parts = bashlex.parse(processed_script)
    except Exception as e:
        # Fall back to treating script as single command if bashlex can't parse it
        # bashlex (a Python port of GNU bash parser) cannot handle bash reserved words
        # like 'time', 'coproc', etc. These are special keywords in bash that have
        # different parsing rules. When bashlex encounters them, it raises an exception.
        error_msg = str(e)

        # bashlex reserved word errors contain "token =" in the message
        # These are valid bash syntax that bashlex can't parse - allow them
        if "token =" in error_msg:
            logger.warning(
                f"bashlex cannot parse bash reserved word. "
                f"Treating script as single command. Error: {e}"
            )
            return [script]

        # Other parsing errors are likely syntax errors - fail fast
        # Common errors: "unexpected EOF", "unexpected token", etc.
        raise ValueError(
            f"Shell syntax error: {e}\n"
            f"Please fix the syntax or use a different approach."
        ) from e

    commands = []
    for part in parts:
        if part.kind == "command":
            command_parts = []
            for word in part.parts:
                start, end = word.pos
                command_parts.append(processed_script[start:end])
            command = " ".join(command_parts)
            commands.append(command)
        elif part.kind in ["function", "pipeline", "list", "compound"]:
            # Find the maximum position including heredoc content
            max_pos = _find_max_heredoc_pos(part, part.pos[1])
            commands.append(processed_script[part.pos[0] : max_pos])
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
