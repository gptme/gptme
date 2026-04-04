"""Shell command validation and safety checks.

Provides allowlist/denylist checking, shellcheck integration, and
quote/heredoc parsing helpers for safe command execution.
"""

import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from ..util.context import md_codeblock

if TYPE_CHECKING:
    from pathlib import Path

    from .base import ToolUse

logger = logging.getLogger(__name__)

# Commands that are safe to auto-approve without user confirmation
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

# Regex to extract command names from pipeline components
cmd_regex = re.compile(r"(?:^|[|&;]|\|\||&&|\n)\s*([^\s|&;]+)")


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

        # Handle escape sequences (only outside single quotes, since
        # bash single-quoted strings treat backslashes as literal)
        if c == "\\" and i + 1 < len(cmd) and not in_single:
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
    return any(start <= pos < end for start, end in quoted_regions)


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
    """Check if a shell command is safe to auto-approve.

    Uses a conservative allowlist approach:
    1. All commands in the pipeline must be in the allowlist
    2. No file redirections (>, >>) - these can write malicious content
    3. No dangerous flags within allowlisted commands (e.g., find -exec)

    This means commands like xargs, sh, bash, python, perl, etc. are automatically
    blocked since they're not in the allowlist, even if piped to from safe commands.
    """
    # Check if all commands in the pipeline are allowlisted
    # This blocks non-allowlisted commands like: python, perl, xargs, sh, bash, etc.
    for match in cmd_regex.finditer(cmd):
        for group in match.groups():
            if group and group not in allowlist_commands:
                return False

    # Check for file redirections (>, >>)
    # File redirections with allowlisted commands can be used to write malicious content
    # Example: echo "malicious_code" > /tmp/exploit.sh
    if _has_file_redirection(cmd):
        return False

    # Check for dangerous flags within allowlisted commands
    # These are rare exceptions where an allowlisted command has dangerous dual-use flags
    # Uses token-based matching (not substring) to avoid false positives like
    # -executable being caught by -exec
    dangerous_flags = {
        "-exec",  # find -exec can execute arbitrary commands
        "-execdir",  # find -execdir can execute arbitrary commands in target dir
        "-delete",  # find -delete can delete files
        "-ok",  # find -ok prompts but can be automated
    }
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    return not any(token in dangerous_flags for token in tokens)


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


def shell_allowlist_hook(
    tool_use: "ToolUse",
    preview: str | None = None,
    workspace: "Path | None" = None,
):
    """Auto-approve hook for allowlisted shell commands.

    This hook is registered with high priority (10) to check allowlisted
    commands before falling through to CLI/server confirmation hooks.

    Returns:
        ConfirmationResult.confirm() for allowlisted commands,
        None to fall through to the next hook for non-allowlisted commands.
    """
    from ..hooks.confirm import ConfirmationResult

    # Only handle shell tool
    if tool_use.tool != "shell":
        return None

    # Get the command from the tool use
    cmd = tool_use.content.strip() if tool_use.content else ""
    if not cmd:
        return None

    # Check if command is allowlisted
    if is_allowlisted(cmd):
        logger.debug(f"Shell command allowlisted, auto-confirming: {cmd[:50]}...")
        return ConfirmationResult.confirm()

    # Not allowlisted - fall through to next hook (CLI/server)
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
            check=False,
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
                message = f"Shellcheck found critical issues that prevent execution:\n{md_codeblock('', output)}\n\nBlocking codes: {codes_str}"
                return True, True, message
            # Non-critical warnings
            message = f"Shellcheck found potential issues:\n{md_codeblock('', output)}"
            return True, False, message

        return False, False, ""
    except (OSError, subprocess.SubprocessError):
        return False, False, ""
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
