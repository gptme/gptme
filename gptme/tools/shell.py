"""
The assistant can execute shell commands with bash by outputting code blocks with `shell` as the language.

Configuration:
    GPTME_SHELL_TIMEOUT: Environment variable to configure command timeout (set before starting gptme)
        - Set to a number (e.g., 30) for timeout in seconds
        - Set to 0 to disable timeout
        - Invalid values default to 1200 seconds (20 minutes)
        - If not set, defaults to 1200 seconds (20 minutes)

    GPTME_SHELL_TRUNC_PRE_TOKENS / GPTME_SHELL_TRUNC_POST_TOKENS: Override the
    head/tail token budget for stdout truncation. Defaults: 2000 / 8000.
    GPTME_SHELL_TRUNC_STDERR_PRE_TOKENS / GPTME_SHELL_TRUNC_STDERR_POST_TOKENS:
    Same overrides for stderr. Defaults: 2000 / 2000. Lowering these makes the
    truncation path fire on smaller outputs, which surfaces savings telemetry
    in `context-savings.jsonl` and the `/context` command. Invalid values fall
    back to defaults.
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
import threading
import time
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

import bashlex

from ..message import Message
from ..util import get_installed_programs
from ..util.ask_execute import execute_with_confirmation
from ..util.context import md_codeblock
from ..util.context_savings import record_context_savings
from ..util.output_storage import save_large_output
from ..util.tokens import get_tokenizer, len_tokens
from .base import (
    Parameter,
    ToolSpec,
    ToolUse,
)
from .shell_background import (
    execute_bg_command,
    execute_jobs_command,
    execute_kill_command,
    execute_output_command,
    get_background_job,
)
from .shell_background import (
    list_background_jobs as list_background_jobs,
)
from .shell_background import (
    reset_background_jobs as reset_background_jobs,
)
from .shell_background import (
    start_background_job as start_background_job,
)
from .shell_validation import (
    _find_first_unquoted_pipe,
    check_with_shellcheck,
    is_allowlisted,
    is_denylisted,
    shell_allowlist_hook,
)

if TYPE_CHECKING:
    from ..hooks import StopPropagation
    from ..logmanager import LogManager

_is_windows = os.name == "nt"

# ANSI escape sequence pattern for stripping terminal formatting
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi_codes(text: str) -> str:
    """Strip ANSI escape sequences from text."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


logger = logging.getLogger(__name__)

# Default token budgets for shell output truncation (overridable via env vars).
_TRUNC_PRE_TOKENS_DEFAULT = 2000
_TRUNC_POST_TOKENS_DEFAULT = 8000
_TRUNC_STDERR_PRE_TOKENS_DEFAULT = 2000
_TRUNC_STDERR_POST_TOKENS_DEFAULT = 2000
_GIT_LOG_PREVIEW_LINES = 20
_GH_LIST_PREVIEW_LINES = 10


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

### Background Jobs

For long-running commands (dev servers, builds, etc.), use background jobs:
- `bg <command>` - Start command in background, returns job ID
- `jobs` - List all background jobs with status
- `output <id>` - Show accumulated output from a job
- `kill <id>` - Terminate a background job

This prevents blocking on commands like `npm run dev` that run indefinitely.
""".strip()

instructions_format: dict[str, str] = {}


def examples(tool_format):
    ls_output = "file1.txt\nfile2.txt"
    ls_files_output = "README.md\nmain.py"
    vue_output = "> npx\n> create-vue\n\nVue.js - The Progressive JavaScript Framework\n\nScaffolding project in ./fancy-project..."
    return f"""
> User: list the current directory
> Assistant: To list the files in the current directory, use `ls`:
{ToolUse("shell", [], "ls").to_output(tool_format)}
> System: Ran command: `ls`
{md_codeblock("stdout", ls_output)}

#### The assistant can learn context by exploring the filesystem

> User: learn about the project
> Assistant: Lets start by checking the files
{ToolUse("shell", [], "git ls-files").to_output(tool_format)}
> System:
{md_codeblock("stdout", ls_files_output)}
> Assistant: Now lets check the README
{ToolUse("shell", [], "cat README.md").to_output(tool_format)}
> System:
{md_codeblock("stdout", "(contents of README.md)")}
> Assistant: Now we check main.py
{ToolUse("shell", [], "cat main.py").to_output(tool_format)}
> System:
{md_codeblock("stdout", "(contents of main.py)")}
> Assistant: The project is...


#### Create vue project

> User: Create a new vue project with typescript and pinia named fancy-project
> Assistant: Sure! Let's create a new vue project with TypeScript and Pinia named fancy-project:
{
        ToolUse(
            "shell",
            [],
            "npm init vue@latest fancy-project --yes -- --typescript --pinia",
        ).to_output(tool_format)
    }
> System:
{md_codeblock("stdout", vue_output)}

#### Proper quoting for complex content

> User: add a comment with backticks and special characters
> Assistant: When passing complex content with special characters, use single quotes to prevent shell interpretation:
{
        ToolUse(
            "shell",
            [],
            "echo 'Content with `backticks` and $variables that should not be interpreted' > example.txt",
        ).to_output(tool_format)
    }

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
    _cwd: str | None  # Workspace directory for this session (thread-safe)

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._init()

        # close on exit
        atexit.register(self.close)

    def _init(self):
        # Choose shell and process group settings based on platform
        if _is_windows:
            shell_cmd = ["bash"]  # Expect MSYS2/Git Bash on Windows
            popen_kwargs: dict = {}  # No start_new_session on Windows
        else:
            shell_cmd = ["bash"]
            popen_kwargs = {
                "start_new_session": True,  # Create new process group for proper signal handling
            }
        self.process = subprocess.Popen(
            shell_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,  # Unbuffered
            universal_newlines=True,
            cwd=self._cwd,  # Use explicit workspace dir (thread-safe)
            **popen_kwargs,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self.stdout_fd = self.process.stdout.fileno()
        self.stderr_fd = self.process.stderr.fileno()
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

    def _needs_tty(self, command: str) -> bool:
        """Check if a command needs a TTY (e.g. sudo password prompt) and we're interactive."""
        if _is_windows:
            return False  # No /dev/tty on Windows
        if not sys.stdin.isatty():
            return False
        # Check for sudo without -S (stdin password) or -n (non-interactive)
        try:
            parts = shlex.split(command)
        except ValueError:
            return False
        # Find sudo in the command (may be preceded by env vars)
        for i, part in enumerate(parts):
            if "=" in part:
                continue  # Skip env var assignments
            if part == "sudo":
                # Check if -S or -n flags are present (they disable TTY need)
                # Also handle combined short flags like -Sn, -nS, -uS
                remaining = parts[i + 1 :]
                flags = [p for p in remaining if p.startswith("-")]
                for flag in flags:
                    if flag in ("--stdin", "--non-interactive"):
                        return False
                    # Check combined short flags (e.g. -Sn, -nS, -uS)
                    if flag.startswith("-") and not flag.startswith("--"):
                        chars = flag[1:]
                        if "S" in chars or "n" in chars:
                            return False
                return True
            break  # First non-env-var token is not sudo
        return False

    def _run_with_tty(
        self, command: str, output: bool = True, timeout: float | None = None
    ) -> tuple[int | None, str, str]:
        """Run a command with /dev/tty as stdin for interactive password prompts (e.g. sudo).

        Used for commands like sudo that need a real terminal to prompt for passwords.
        Runs as a separate subprocess in the current working directory.
        """
        logger.debug(f"Shell: Running with TTY stdin: {command[:200]}")
        try:
            tty_stdin = open("/dev/tty", "rb")
        except OSError:
            logger.warning("Could not open /dev/tty, falling back to normal run")
            return self._run_pipe(command, output=output, timeout=timeout)

        try:
            # Inherit session env overrides so sudo commands behave consistently
            # (e.g. no pager, consistent EDITOR) with commands run via _run_pipe
            session_env = os.environ.copy()
            session_env.update(
                {
                    "PAGER": "",
                    "GH_PAGER": "",
                    "GIT_PAGER": "cat",
                    "EDITOR": "true",
                    "GIT_EDITOR": "true",
                    "VISUAL": "true",
                    "PYTHONUNBUFFERED": "1",
                }
            )
            proc = subprocess.Popen(
                ["bash", "-c", command],
                stdin=tty_stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # Don't use start_new_session=True here - we need to inherit the
                # controlling terminal so sudo can prompt for passwords
                cwd=self._cwd or os.getcwd(),
                env=session_env,
            )
            try:
                stdout_data, stderr_data = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_data, stderr_data = proc.communicate()
                stdout_str = stdout_data.decode("utf-8", errors="replace").strip()
                stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
                return -124, stdout_str, stderr_str
            except KeyboardInterrupt:
                proc.kill()
                proc.communicate()
                raise
        finally:
            tty_stdin.close()

        stdout_str = stdout_data.decode("utf-8", errors="replace").strip()
        stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
        if output:
            if stdout_str:
                print(stdout_str, file=sys.stdout)
            if stderr_str:
                print(stderr_str, file=sys.stderr)
        return proc.returncode, stdout_str, stderr_str

    def _run(
        self, command: str, output=True, tries=0, timeout: float | None = None
    ) -> tuple[int | None, str, str]:
        # Use TTY-based execution for interactive sudo commands
        if self._needs_tty(command):
            return self._run_with_tty(command, output=output, timeout=timeout)
        return self._run_pipe(command, output=output, tries=tries, timeout=timeout)

    def _run_pipe(
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
        if _is_windows:
            # Windows: use non-blocking read to drain stderr
            try:
                os.set_blocking(self.stderr_fd, False)
                while True:
                    try:
                        pre_drain_data = os.read(self.stderr_fd, 2**16).decode(
                            "utf-8", errors="replace"
                        )
                        if not pre_drain_data:
                            break
                        if pre_drain_data.strip():
                            logger.debug(
                                f"Shell: Pre-command stderr drain: {pre_drain_data[:80]}"
                            )
                    except BlockingIOError:
                        break
                os.set_blocking(self.stderr_fd, True)
            except OSError:
                pass
        else:
            assert select is not None
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
                    logger.debug(
                        f"Shell: Pre-command stderr drain: {pre_drain_data[:80]}"
                    )

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
                return self._run_pipe(
                    command, output=output, tries=tries + 1, timeout=timeout
                )
            raise

        self.process.stdin.flush()

        # Issue #408: Track whether we've seen the start marker for this command
        seen_start_marker = False

        stdout: list[str] = []
        stderr: list[str] = []
        return_code: int | None = None
        start_time = time.time() if timeout else None

        if _is_windows:
            return self._read_output_windows(
                command,
                output,
                stdout,
                stderr,
                return_code,
                seen_start_marker,
                start_marker_pattern,
                start_time,
                timeout,
            )
        return self._read_output_unix(
            command,
            output,
            stdout,
            stderr,
            return_code,
            seen_start_marker,
            start_marker_pattern,
            start_time,
            timeout,
        )

    def _read_output_windows(
        self,
        command: str,
        output: bool,
        stdout: list[str],
        stderr: list[str],
        return_code: int | None,
        seen_start_marker: bool,
        start_marker_pattern: str,
        start_time: float | None,
        timeout: float | None,
    ) -> tuple[int | None, str, str]:
        """Read command output on Windows using threads with non-blocking I/O."""
        from queue import Empty, Queue

        stdout_queue: Queue[str] = Queue()
        stderr_queue: Queue[str] = Queue()
        stop_event = threading.Event()

        def read_stream(fd: int, queue: Queue[str]) -> None:
            try:
                os.set_blocking(fd, False)
            except OSError:
                pass
            while not stop_event.is_set():
                try:
                    data = os.read(fd, 2**16).decode("utf-8", errors="replace")
                    if not data:
                        break
                    queue.put(data)
                except BlockingIOError:
                    time.sleep(0.01)
                except OSError:
                    break

        t_stdout = threading.Thread(
            target=read_stream, args=(self.stdout_fd, stdout_queue), daemon=True
        )
        t_stderr = threading.Thread(
            target=read_stream, args=(self.stderr_fd, stderr_queue), daemon=True
        )
        t_stdout.start()
        t_stderr.start()

        re_returncode = re.compile(r"ReturnCode:(\d+)")

        try:
            while not stop_event.is_set():
                # Check timeout
                if timeout and start_time:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        logger.info(f"Command timed out after {timeout} seconds")
                        self._terminate_process()
                        stop_event.set()
                        return (
                            -124,
                            "".join(stdout).strip(),
                            "".join(stderr).strip(),
                        )

                # Drain stdout queue
                try:
                    data = stdout_queue.get(timeout=0.1)
                    lines = data.splitlines(keepends=True)
                    for line in lines:
                        if not seen_start_marker:
                            if start_marker_pattern in line:
                                seen_start_marker = True
                                logger.debug(
                                    f"Shell: Start marker detected: {start_marker_pattern[:50]}"
                                )
                            else:
                                if line.strip():
                                    logger.debug(
                                        f"Shell: Discarding pre-marker output: {line[:80]}"
                                    )
                            continue

                        if "ReturnCode:" in line and self.delimiter in line:
                            # Extract any command output that precedes the
                            # delimiter on the same line.  This happens when
                            # command output lacks a trailing newline (e.g.
                            # printf "yes", echo -n "data").
                            # Use rfind to get the LAST "ReturnCode:" occurrence,
                            # which is always the shell-injected marker (not
                            # command output that itself contains "ReturnCode:").
                            rc_pos = line.rfind("ReturnCode:")
                            if rc_pos > 0:
                                prefix = line[:rc_pos]
                                stdout.append(prefix)
                                if output:
                                    print(prefix, end="", file=sys.stdout)

                            logger.debug(
                                f"Shell: Delimiter detected in line: {line.strip()[:200]}"
                            )
                            # Use findall+last to avoid matching "ReturnCode:N"
                            # in command output that precedes the marker.
                            rc_matches = re_returncode.findall(line)
                            if rc_matches:
                                return_code = int(rc_matches[-1])
                            if command.startswith("cd ") and return_code == 0:
                                ex, pwd, _ = self._run("pwd", output=False)
                                if ex == 0:
                                    os.chdir(pwd.strip())

                            # Drain remaining stderr
                            stop_event.set()
                            time.sleep(0.05)
                            while not stderr_queue.empty():
                                try:
                                    err_data = stderr_queue.get_nowait()
                                    stderr.append(err_data)
                                    if output:
                                        print(err_data, end="", file=sys.stderr)
                                except Empty:
                                    break
                            return (
                                return_code,
                                "".join(stdout).strip(),
                                "".join(stderr).strip(),
                            )

                        stdout.append(line)
                        if output:
                            print(line, end="", file=sys.stdout)
                except Empty:
                    pass

                # Drain stderr queue
                try:
                    data = stderr_queue.get_nowait()
                    lines = data.splitlines(keepends=True)
                    for line in lines:
                        stderr.append(line)
                        if output:
                            print(line, end="", file=sys.stderr)
                except Empty:
                    pass

                # If both reader threads are dead, stop
                if not t_stdout.is_alive() and not t_stderr.is_alive():
                    break

        except KeyboardInterrupt:
            print()
            logger.info("Process interrupted during output reading")
            partial_stdout = "".join(stdout).strip()
            partial_stderr = "".join(stderr).strip()
            raise KeyboardInterrupt((partial_stdout, partial_stderr)) from None
        finally:
            stop_event.set()
            t_stdout.join(timeout=0.5)
            t_stderr.join(timeout=0.5)

        # Fallback: if we get here without finding delimiter, return what we have
        return (return_code, "".join(stdout).strip(), "".join(stderr).strip())

    def _read_output_unix(
        self,
        command: str,
        output: bool,
        stdout: list[str],
        stderr: list[str],
        return_code: int | None,
        seen_start_marker: bool,
        start_marker_pattern: str,
        start_time: float | None,
        timeout: float | None,
    ) -> tuple[int | None, str, str]:
        """Read command output on Unix using select()."""
        assert select is not None
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
                    # We use a higher value, because there is a bug which leads to
                    # spaces at the boundary
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
                                    f"Shell: Start marker detected: "
                                    f"{start_marker_pattern[:50]}"
                                )
                            else:
                                # Discard output before start marker (leftover
                                # from previous commands)
                                if line.strip():  # Only log non-empty lines
                                    logger.debug(
                                        f"Shell: Discarding pre-marker output: "
                                        f"{line[:80]}"
                                    )
                            continue

                        if "ReturnCode:" in line and self.delimiter in line:
                            # Extract any command output that precedes the
                            # delimiter on the same line.  This happens when
                            # command output lacks a trailing newline (e.g.
                            # printf "yes", echo -n "data").
                            # Use rfind to get the LAST "ReturnCode:" occurrence,
                            # which is always the shell-injected marker (not
                            # command output that itself contains "ReturnCode:").
                            rc_pos = line.rfind("ReturnCode:")
                            if rc_pos > 0:
                                prefix = line[:rc_pos]
                                stdout.append(prefix)
                                if output:
                                    print(prefix, end="", file=sys.stdout)

                            # Diagnostic logging for Issue #408
                            logger.debug(
                                f"Shell: Delimiter detected in line: "
                                f"{line.strip()[:200]}"
                            )

                            # Capture last stdout before delimiter
                            if stdout:
                                last_lines = stdout[-3:] if len(stdout) >= 3 else stdout
                                last_stdout = "".join(last_lines).strip()[:300]
                                logger.debug(
                                    f"Shell: Last stdout before delimiter: "
                                    f"{last_stdout}"
                                )

                            # Use findall+last to avoid matching "ReturnCode:N"
                            # in command output that precedes the marker.
                            rc_matches = re_returncode.findall(line)
                            if rc_matches:
                                return_code = int(rc_matches[-1])
                            # if command is cd, update working directory
                            if command.startswith("cd ") and return_code == 0:
                                ex, pwd, _ = self._run("pwd", output=False)
                                if ex != 0:
                                    logger.warning(
                                        "pwd failed after cd, cannot update "
                                        "working directory"
                                    )
                                else:
                                    os.chdir(pwd.strip())

                            # Issue #408: Drain any remaining stderr before
                            # returning. Use multiple attempts to ensure stderr
                            # has time to arrive from bash.
                            drain_empty_count = 0
                            while drain_empty_count < 2:
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
            partial_stdout = "".join(stdout).strip()
            partial_stderr = "".join(stderr).strip()
            raise KeyboardInterrupt((partial_stdout, partial_stderr)) from None

    def _terminate_process(self) -> None:
        """Terminate the shell process, platform-aware."""
        try:
            if _is_windows:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
            else:
                pgid = os.getpgid(self.process.pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.1)
                if self.process.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as e:
            logger.warning(f"Error terminating process: {e}")

    def close(self):
        # Close stdin to signal no more input
        if self.process.stdin:
            self.process.stdin.close()

        # Terminate the process
        self._terminate_process()

        # Close stdout/stderr AFTER process is terminated to prevent broken pipes
        if self.process.stdout:
            self.process.stdout.close()
        if self.process.stderr:
            self.process.stderr.close()

    def restart(self):
        self.close()
        self._init()


_shell_var: ContextVar[ShellSession | None] = ContextVar("shell", default=None)
_workspace_cwd: ContextVar[str | None] = ContextVar("workspace_cwd", default=None)

# Conversation-level shell registry for server-side cleanup.
# Maps conversation_id -> ShellSession so SESSION_END hooks can find and close
# shells that would otherwise leak when their thread's ContextVar goes out of scope.
_conversation_shells: dict[str, ShellSession] = {}
_conv_shell_lock: threading.Lock = threading.Lock()


def set_workspace_cwd(cwd: str) -> None:
    """Set the workspace directory for the current context (thread-safe).

    Call this before any shell creation to ensure the shell subprocess
    starts in the correct directory, even with concurrent sessions.
    This is the thread-safe replacement for os.chdir() in server contexts.
    """
    _workspace_cwd.set(cwd)


def get_workspace_cwd() -> str | None:
    """Get the workspace directory for the current context, if set."""
    return _workspace_cwd.get()


def get_shell() -> ShellSession:
    """Get the shell session for the current context, creating it if necessary.

    Uses ContextVar to provide context-local state, allowing each conversation
    to have its own shell session with independent working directory.

    In server contexts (where current_conversation_id is set), also registers
    the shell in a conversation-level registry for cleanup via SESSION_END hooks.
    """
    shell = _shell_var.get()
    if shell is None:
        # Use workspace from ContextVar for thread-safe cwd
        workspace = _workspace_cwd.get()
        shell = ShellSession(cwd=workspace)
        _shell_var.set(shell)
        # Register for conversation-level cleanup if in a server context
        _register_conversation_shell(shell)
    return shell


def set_shell(shell: ShellSession) -> None:
    """Set the shell session for the current context (for testing)."""
    _shell_var.set(shell)


def _register_conversation_shell(shell: ShellSession) -> None:
    """Register a shell in the conversation-level registry if a conversation context exists."""
    try:
        from ..hooks import current_conversation_id

        conv_id = current_conversation_id.get()
        if conv_id is not None:
            with _conv_shell_lock:
                # Close any existing shell for this conversation before replacing
                old_shell = _conversation_shells.get(conv_id)
                if old_shell is not None and old_shell is not shell:
                    try:
                        old_shell.close()
                    except Exception as e:
                        logger.warning(
                            f"Error closing old shell for conversation {conv_id}: {e}"
                        )
                _conversation_shells[conv_id] = shell
    except ImportError:
        pass  # hooks module not available (e.g., during testing)


def close_conversation_shell(conversation_id: str) -> None:
    """Close and remove the shell session for a conversation.

    Called by the SESSION_END hook to clean up shell file descriptors
    when a conversation's last session is removed.
    """
    with _conv_shell_lock:
        shell = _conversation_shells.pop(conversation_id, None)
    if shell is not None:
        try:
            shell.close()
            logger.debug(f"Closed shell session for conversation {conversation_id}")
        except Exception as e:
            logger.warning(
                f"Error closing shell for conversation {conversation_id}: {e}"
            )


def _session_end_shell_cleanup(
    manager: "LogManager", **kwargs
) -> "Generator[Message | StopPropagation, None, None]":
    """Close shell session for a conversation to prevent file descriptor leaks.

    Registered as a SESSION_END hook via ToolSpec.hooks so it's only loaded
    when the shell tool is active.
    """
    conversation_id = manager.logdir.name if manager.logdir else None
    if conversation_id:
        close_conversation_shell(conversation_id)

    yield from ()


def get_shell_command(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> str:
    """Get the shell command from code/args/kwargs."""
    if code is not None and args is not None:
        assert not args
        cmd = code.strip()
        cmd = cmd.removeprefix("$ ")
    elif kwargs is not None:
        cmd = kwargs.get("command", "")
    else:
        raise ValueError("No command provided")
    return cmd


def preview_shell(cmd: str, _: Path | None) -> str:
    """Prepare preview for shell command."""
    return cmd


def _get_truncation_budget(
    pre_env: str,
    post_env: str,
    default_pre: int,
    default_post: int,
) -> tuple[int, int]:
    """Resolve head/tail token budgets for output truncation.

    Reads ``pre_env`` and ``post_env`` env vars, falling back to defaults on
    missing or invalid values. Negative or zero values fall back too — the
    truncation path requires both to be positive.

    Env vars are re-read on every call so that users can adjust thresholds at
    runtime (e.g. via a shell alias) without restarting gptme.
    """

    def _resolve(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            logger.warning("Invalid %s value: %r, using default %d", name, raw, default)
            return default
        if value <= 0:
            logger.warning(
                "Non-positive %s value: %d, using default %d", name, value, default
            )
            return default
        return value

    return _resolve(pre_env, default_pre), _resolve(post_env, default_post)


def _default_model_name() -> str:
    from ..llm.models import get_default_model  # fmt: skip

    model = get_default_model()
    return model.model if model else "cl100k_base"


def _matches_git_log_oneline(cmd: str) -> bool:
    if any(ch in cmd for ch in "\n|;&<>`$"):
        return False

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False

    if len(tokens) < 3 or tokens[:2] != ["git", "log"]:
        return False

    return any(token == "--oneline" for token in tokens[2:])


def _matches_gh_list(cmd: str) -> bool:
    """Detect ``gh issue list`` or ``gh pr list`` commands."""
    if any(ch in cmd for ch in "\n|;&<>`$"):
        return False

    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False

    if len(tokens) < 3 or tokens[0] != "gh":
        return False

    return tokens[1] in ("issue", "pr") and tokens[2] == "list"


def _format_git_log_preview(cmd: str, stdout: str, logdir: Path | None) -> str | None:
    lines = [line for line in strip_ansi_codes(stdout).splitlines() if line.strip()]
    if len(lines) <= _GIT_LOG_PREVIEW_LINES:
        return None

    saved_path: Path | None = None
    if logdir:
        _, saved_path = save_large_output(
            content=stdout,
            logdir=logdir,
            output_type="shell",
            command_info=cmd,
        )

    omitted = len(lines) - _GIT_LOG_PREVIEW_LINES
    preview = "\n".join(
        lines[:_GIT_LOG_PREVIEW_LINES] + [f"... ({omitted} more commits omitted) ..."]
    )

    detail = f"Showing first {_GIT_LOG_PREVIEW_LINES} of {len(lines)} commits."
    if saved_path:
        detail += (
            f" Full output saved to {saved_path}; read that file for the complete list."
        )
    else:
        detail += (
            " Full output was not saved because no conversation logdir is active."
            " To get the full list, pipe through cat: `git log --oneline | cat`."
        )
    detail += " Use `git show <sha>` for a specific commit."

    body = detail + "\n\n" + md_codeblock("stdout", preview)

    if logdir and saved_path:
        model_name = _default_model_name()
        try:
            record_context_savings(
                logdir=logdir,
                source="shell",
                original_tokens=len_tokens(stdout, model_name),
                kept_tokens=len_tokens(body, model_name),
                command_info=f"git_log_oneline: {cmd}",
                saved_path=saved_path,
            )
        except OSError as e:
            logger.warning("Failed to record compact shell telemetry: %s", e)

    return body


def _format_gh_list_preview(cmd: str, stdout: str, logdir: Path | None) -> str | None:
    """Format a compact preview for ``gh issue list`` / ``gh pr list`` output."""
    lines = [line for line in strip_ansi_codes(stdout).splitlines() if line.strip()]
    if len(lines) <= _GH_LIST_PREVIEW_LINES:
        return None

    saved_path: Path | None = None
    if logdir:
        _, saved_path = save_large_output(
            content=stdout,
            logdir=logdir,
            output_type="shell",
            command_info=cmd,
        )

    omitted = len(lines) - _GH_LIST_PREVIEW_LINES
    preview = "\n".join(
        lines[:_GH_LIST_PREVIEW_LINES] + [f"... ({omitted} more items omitted) ..."]
    )

    detail = f"Showing first {_GH_LIST_PREVIEW_LINES} of {len(lines)} items."
    if saved_path:
        detail += (
            f" Full output saved to {saved_path}; read that file for the complete list."
        )
    else:
        detail += (
            " Full output was not saved because no conversation logdir is active."
            " To get the full list, pipe through cat: `gh issue list | cat`."
        )
    detail += " Use `gh issue view <number>` or `gh pr view <number>` for details."

    body = detail + "\n\n" + md_codeblock("stdout", preview)

    if logdir and saved_path:
        model_name = _default_model_name()
        try:
            record_context_savings(
                logdir=logdir,
                source="shell",
                original_tokens=len_tokens(stdout, model_name),
                kept_tokens=len_tokens(body, model_name),
                command_info=f"gh_list: {cmd}",
                saved_path=saved_path,
            )
        except OSError as e:
            logger.warning("Failed to record compact shell telemetry: %s", e)

    return body


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

    compact_stdout = None
    if (
        returncode == 0
        and stdout
        and not stderr
        and not interrupted
        and not timed_out
        and _matches_git_log_oneline(cmd)
    ):
        try:
            compact_stdout = _format_git_log_preview(cmd, stdout, logdir)
        except OSError as e:
            logger.warning("Failed to format compact shell output: %s", e)

    if compact_stdout is None and (
        returncode == 0
        and stdout
        and not stderr
        and not interrupted
        and not timed_out
        and _matches_gh_list(cmd)
    ):
        try:
            compact_stdout = _format_gh_list_preview(cmd, stdout, logdir)
        except OSError as e:
            logger.warning("Failed to format compact shell output: %s", e)

    if compact_stdout is None:
        # Apply shortening logic with output storage
        pre_tokens, post_tokens = _get_truncation_budget(
            "GPTME_SHELL_TRUNC_PRE_TOKENS",
            "GPTME_SHELL_TRUNC_POST_TOKENS",
            default_pre=_TRUNC_PRE_TOKENS_DEFAULT,
            default_post=_TRUNC_POST_TOKENS_DEFAULT,
        )
        stderr_pre_tokens, stderr_post_tokens = _get_truncation_budget(
            "GPTME_SHELL_TRUNC_STDERR_PRE_TOKENS",
            "GPTME_SHELL_TRUNC_STDERR_POST_TOKENS",
            default_pre=_TRUNC_STDERR_PRE_TOKENS_DEFAULT,
            default_post=_TRUNC_STDERR_POST_TOKENS_DEFAULT,
        )
        stdout = _shorten_stdout(
            stdout,
            pre_tokens=pre_tokens,
            post_tokens=post_tokens,
            logdir=logdir,
            cmd=cmd,
        )
        stderr = _shorten_stdout(
            stderr,
            pre_tokens=stderr_pre_tokens,
            post_tokens=stderr_post_tokens,
            logdir=logdir,
            cmd=f"{cmd} (stderr)",
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
    if compact_stdout:
        msg += compact_stdout + "\n\n"
    elif stdout:
        msg += _format_block_smart("", stdout, "stdout").lstrip() + "\n\n"
    if stderr:
        msg += _format_block_smart("", stderr, "stderr").lstrip() + "\n\n"
    if not compact_stdout and not stdout and not stderr:
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


def execute_shell_impl(
    cmd: str, logdir: Path | None, timeout: float | None = None
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

        _terminate_interrupted_shell(shell)

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
        raise KeyboardInterrupt from None


def _terminate_interrupted_shell(
    shell: ShellSession, log_context: str = "Shell command"
) -> None:
    logger.info("%s interrupted, sending SIGINT to subprocess", log_context)
    try:
        if _is_windows:
            shell.process.terminate()
            shell.process.wait(timeout=2.0)
        else:
            pgid = os.getpgid(shell.process.pid)
            os.killpg(pgid, signal.SIGINT)
            shell.process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        logger.info("Process didn't exit gracefully, terminating")
        try:
            if _is_windows:
                shell.process.kill()
            else:
                pgid = os.getpgid(shell.process.pid)
                os.killpg(pgid, signal.SIGTERM)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Error terminating interrupted process: %s", e)


def get_path_fn(*args, **kwargs) -> Path | None:
    from ..logmanager import LogManager  # fmt: skip

    manager = LogManager.get_current_log()
    return manager.logdir if manager and manager.logdir else None


def _execute_preceding_commands(
    cmds: str,
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
            output_parts.append(md_codeblock("stdout", stdout.strip()))
        if stderr and stderr.strip():
            output_parts.append(md_codeblock("stderr", stderr.strip()))

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


def _get_timeout() -> float | None:
    timeout: float | None = 1200.0
    timeout_env = os.environ.get("GPTME_SHELL_TIMEOUT")
    if timeout_env is not None:
        try:
            timeout = float(timeout_env)
            if timeout <= 0:
                timeout = None
        except ValueError:
            logger.warning(
                "Invalid GPTME_SHELL_TIMEOUT value: %s, using default 1200s (20 minutes)",
                timeout_env,
            )
            timeout = 1200.0
    return timeout


def execute_shell(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
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
                yield from _execute_preceding_commands(preceding_cmds)
            # Now execute the bg command
            bg_line = lines[bg_line_idx].strip()
            bg_cmd = bg_line[3:].strip()  # Remove "bg " prefix
            yield from execute_bg_command(bg_cmd)
            # Execute any remaining commands after bg (unlikely but handle it)
            if bg_line_idx < len(lines) - 1:
                remaining_cmds = "\n".join(lines[bg_line_idx + 1 :])
                if remaining_cmds.strip():
                    yield from execute_shell(remaining_cmds, None, None)
            return
        else:
            # bg is first line but there are more lines after it
            # Start bg job, then execute remaining commands
            bg_line = lines[0].strip()
            bg_cmd = bg_line[3:].strip()
            yield from execute_bg_command(bg_cmd)
            remaining_cmds = "\n".join(lines[1:])
            if remaining_cmds.strip():
                yield from execute_shell(remaining_cmds, None, None)
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

    timeout = _get_timeout()

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
        logger.debug(f"Command allowlisted, skipping confirmation: {cmd[:80]}")
        logdir = get_path_fn()
        yield from execute_shell_impl(cmd, logdir, timeout=timeout)
    else:
        logger.debug(f"Command not allowlisted, requiring confirmation: {cmd[:80]}")

        # Create a wrapper function that passes timeout to execute_shell_impl
        def execute_fn(cmd: str, path: Path | None) -> Generator[Message, None, None]:
            return execute_shell_impl(cmd, path, timeout=timeout)

        yield from execute_with_confirmation(
            cmd,
            args,
            kwargs,
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
    tokenizer = None
    tokens: list[int] = []
    # Resolved lazily on first use, reused by tokenizer + savings-recording paths.
    model_name: str | None = None
    if pre_tokens is not None and post_tokens is not None:
        from ..llm.models import get_default_model  # fmt: skip

        model = get_default_model()
        model_name = model.model if model else "gpt-4"
        tokenizer = get_tokenizer(model_name)
        if tokenizer is not None:
            tokens = tokenizer.encode(stdout)
            will_truncate_by_tokens = len(tokens) > pre_tokens + post_tokens
        else:
            # Char-based approximation (~4 chars/token) when tokenizer unavailable
            will_truncate_by_tokens = len(stdout) > (pre_tokens + post_tokens) * 4

    # If truncation will happen, save full output to file
    saved_path = None
    if (will_truncate_by_lines or will_truncate_by_tokens) and logdir:
        command_info = f"Command: {cmd}" if cmd else None
        original_tokens = (
            len(tokens) if (will_truncate_by_tokens and tokenizer is not None) else None
        )
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
        if not will_truncate_by_tokens and tokenizer is None:
            # tokenizer may still be unavailable (char-based estimate used above);
            # try once more for a precise check before deciding not to truncate.
            from ..llm.models import get_default_model  # fmt: skip

            model = get_default_model()
            tokenizer = get_tokenizer(model.model if model else "gpt-4")
            if tokenizer is not None:
                tokens = tokenizer.encode(stdout)
        if tokenizer is not None and len(tokens) > pre_tokens + post_tokens:
            truncation_msg = "... (output truncated"
            if saved_path:
                truncation_msg += f", full output saved to {saved_path}"
            truncation_msg += ") ..."
            lines = (
                [tokenizer.decode(tokens[:pre_tokens])]
                + [truncation_msg]
                + [tokenizer.decode(tokens[-post_tokens:])]
            )
        elif tokenizer is None and will_truncate_by_tokens:
            # Char-based fallback when tokenizer unavailable (~4 chars/token)
            pre_chars = pre_tokens * 4
            post_chars = post_tokens * 4
            truncation_msg = "... (output truncated"
            if saved_path:
                truncation_msg += f", full output saved to {saved_path}"
            truncation_msg += ") ..."
            lines = [stdout[:pre_chars]] + [truncation_msg] + [stdout[-post_chars:]]

    result = "\n".join(lines)

    if saved_path and logdir:
        if model_name is None:
            from ..llm.models import get_default_model  # fmt: skip

            model = get_default_model()
            model_name = model.model if model else "gpt-4"
        record_context_savings(
            logdir=logdir,
            source="shell",
            original_tokens=len_tokens(stdout, model_name),
            kept_tokens=len_tokens(result, model_name),
            command_info=cmd,
            saved_path=saved_path,
        )

    return result


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
    # Register shell allowlist hook with high priority (10)
    # This auto-confirms allowlisted commands before CLI/server hooks (priority 0)
    hooks={
        "allowlist": ("tool.confirm", shell_allowlist_hook, 10),
        "session_end": ("session.end", _session_end_shell_cleanup, 0),
    },
)
__doc__ = tool.get_doc(__doc__)
