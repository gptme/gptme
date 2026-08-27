"""Tests for shell tool auto-approval of allowlisted commands.

Regression test for issue where read-only commands like `cat file | head -100`
were requiring confirmation despite being in the allowlist.
"""

from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.base import ToolUse
from gptme.tools.shell import (
    execute_shell,
    is_allowlisted,
    shell_allowlist_hook,
)

# Test cases: (command, should_be_allowlisted, description)
ALLOWLIST_TEST_CASES = [
    # Simple read-only commands - should be allowlisted
    ("cat README.md", True, "simple cat"),
    ("head -100 file.txt", True, "simple head"),
    ("ls", True, "simple ls"),
    ("ls -la", True, "ls with flags"),
    ("ls -la /tmp", True, "ls with path"),
    ("pwd", True, "pwd"),
    ("tree -L 2", True, "tree"),
    ("rg pattern", True, "ripgrep"),
    ("rg pattern file.txt", True, "ripgrep with file"),
    ("find . -name '*.py'", True, "find by name"),
    ("grep pattern file", True, "grep"),
    ("wc -l file.txt", True, "word count"),
    # Pipelines of allowlisted commands - should be allowlisted
    ("cat gptme/cli/commands.py | head -100", True, "cat piped to head"),
    ("grep pattern file | sort | head -10", True, "grep-sort-head pipeline"),
    ("cat file | grep pattern", True, "cat-grep pipeline"),
    ("find . -name '*.py' | wc -l", True, "find-wc pipeline"),
    # Commands with output redirection - should NOT be allowlisted
    ("cat file > output.txt", False, "cat with redirection"),
    ("echo 'hello' > output.txt", False, "echo with redirection"),
    ("ls > files.txt", False, "ls with redirection"),
    ("grep pattern file >> output.txt", False, "grep with append"),
    # Non-allowlisted commands - should NOT be allowlisted
    ("rm -rf /tmp/foo", False, "rm command"),
    ("python script.py", False, "python command"),
    ("npm install", False, "npm command"),
    # Pipes to non-allowlisted commands - blocked by allowlist (not in allowlist)
    ("cat file | xargs rm", False, "pipe to xargs (not in allowlist)"),
    ("grep pattern file | xargs python", False, "pipe to xargs"),
    ("cat file | sh", False, "pipe to sh (not in allowlist)"),
    ("head file | bash", False, "pipe to bash (not in allowlist)"),
    ("ls | python -c 'import sys'", False, "pipe to python"),
    ("cat data.csv | perl -lane", False, "pipe to perl"),
    # Safe find flags that look like dangerous ones - should be allowlisted
    ("find . -executable", True, "find -executable (safe flag, not -exec)"),
    (
        "find . -type f -executable -name '*.sh'",
        True,
        "find -executable with other flags",
    ),
    # Dangerous flags within allowlisted commands - blocked by flag check
    ("find . -name '*.py' -exec rm {} \\;", False, "find -exec rm (dangerous flag)"),
    ("find . -type f -exec cat {} \\;", False, "find -exec cat (dangerous flag)"),
    (
        "find / -name passwd -exec cat {} \\;",
        False,
        "find -exec to read sensitive files",
    ),
    (
        "find . -name '*.log' -execdir rm {} \\;",
        False,
        "find -execdir (dangerous flag)",
    ),
    ("find /tmp -type f -delete", False, "find -delete (dangerous flag)"),
    ("find . -name '*.txt' -ok cat {} \\;", False, "find -ok (dangerous flag)"),
    # Quoted dangerous flags should still be caught (shlex handles quoting)
    ("find . '-exec' rm {} \\;", False, "find with quoted -exec (bypass attempt)"),
    (
        'find . "-exec" rm {} \\;',
        False,
        "find with double-quoted -exec (bypass attempt)",
    ),
]


class TestIsAllowlisted:
    """Tests for the is_allowlisted function."""

    @pytest.mark.parametrize(("cmd", "expected", "description"), ALLOWLIST_TEST_CASES)
    def test_allowlist_cases(self, cmd: str, expected: bool, description: str):
        """Test various allowlist cases."""
        result = is_allowlisted(cmd)
        assert result == expected, f"Failed for {description}: {cmd}"


class TestShellAllowlistHook:
    """Tests for the shell_allowlist_hook function."""

    def test_allowlisted_command_auto_confirms(self):
        """Test that allowlisted shell commands auto-confirm via hook."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="cat README.md | head -50",
        )

        result = shell_allowlist_hook(tool_use)

        assert result is not None
        assert result.action.value == "confirm"

    def test_allowlisted_pipe_command_auto_confirms(self):
        """Test that piped allowlisted commands auto-confirm via hook."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="cat gptme/cli/commands.py | head -100",
        )

        result = shell_allowlist_hook(tool_use)

        assert result is not None
        assert result.action.value == "confirm"

    def test_non_allowlisted_command_falls_through(self):
        """Test that non-allowlisted commands fall through (return None)."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="python script.py",
        )

        result = shell_allowlist_hook(tool_use)

        # Should return None to fall through to CLI/server hooks
        assert result is None

    def test_non_shell_tool_falls_through(self):
        """Test that non-shell tools fall through."""
        tool_use = ToolUse(
            tool="python",
            args=[],
            kwargs={},
            content="print('hello')",
        )

        result = shell_allowlist_hook(tool_use)

        # Should return None for non-shell tools
        assert result is None

    def test_empty_command_falls_through(self):
        """Test that empty commands fall through."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="",
        )

        result = shell_allowlist_hook(tool_use)

        # Should return None for empty commands
        assert result is None


class TestExecuteShellAllowlist:
    """Tests for the actual execute_shell function's allowlist behavior."""

    @pytest.fixture
    def mock_shell(self):
        """Create a mock shell session."""
        with patch("gptme.tools.shell.get_shell") as mock:
            shell = MagicMock()
            shell.run.return_value = (0, "output", "")
            mock.return_value = shell
            yield shell

    @pytest.fixture
    def mock_logdir(self, tmp_path):
        """Create a temporary log directory."""
        with patch("gptme.tools.shell.get_path_fn") as mock:
            mock.return_value = tmp_path
            yield tmp_path

    def test_allowlisted_command_executes_without_confirmation(
        self, mock_shell, mock_logdir
    ):
        """Test that allowlisted commands execute without user prompting.

        After the fix in gptme#3598, all shell commands go through
        execute_with_confirmation() so that TOOL_CONFIRM guardrails can run.
        The shell_allowlist_hook (priority=10) auto-confirms safe commands, so
        the user is never prompted — the behaviour is unchanged from the outside,
        but the implementation now routes through the hook chain.
        """
        cmd = "cat README.md | head -100"

        # Execute the command - goes through the hook chain, auto-confirmed by
        # shell_allowlist_hook; no user prompt appears.
        messages = list(execute_shell(cmd, [], None))

        # The command must have actually executed (mock_shell.run was called).
        assert mock_shell.run.called, "Shell command did not execute"

        # At least one message should come back from the execution.
        assert len(messages) >= 1

    def test_non_allowlisted_command_uses_confirmation(self, mock_shell, mock_logdir):
        """Test that non-allowlisted commands use confirmation hook."""
        cmd = "python script.py"

        # Mock get_confirmation to return confirm result
        with patch("gptme.tools.shell.execute_with_confirmation") as mock_exec_confirm:
            # Make execute_with_confirmation yield a message
            def mock_gen(*args, **kwargs):
                yield Message("system", "Executed via confirmation")

            mock_exec_confirm.return_value = mock_gen()

            # Execute the command - args must be [] not None for code path
            result = list(execute_shell(cmd, [], None))

            # execute_with_confirmation SHOULD be called for non-allowlisted commands
            mock_exec_confirm.assert_called_once()
            # Result should be the message from our mock
            assert len(result) == 1

    def test_bg_allowlisted_command_auto_confirms(self):
        """bg <allowlisted> should auto-confirm — not prompt the user.

        Regression: before this fix, is_allowlisted("bg ls") returned False
        because "bg" is not a shell command, causing a behavior regression
        where previously-silent bg-wrapped allowlisted commands started
        prompting the user.
        """
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="bg ls",
        )

        # When no surrounding commands exist the hook should see "ls" and
        # auto-confirm.  preview=None so the hook falls back to content.
        result = shell_allowlist_hook(tool_use, preview="bg ls")

        assert result is not None, (
            "shell_allowlist_hook must auto-confirm 'bg ls' — 'ls' is allowlisted"
        )
        assert result.action.value == "confirm"

    @pytest.mark.parametrize(
        "preview",
        [
            "ls\nbg pwd",
            "bg ls\npwd",
            "ls\nbg pwd\nhead README.md",
            "echo hi; bg ls",
        ],
    )
    def test_bg_with_allowlisted_surrounding_commands_auto_confirms(self, preview):
        """Safe multi-line bg sequences retain their pre-hook behavior."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content=preview,
        )

        result = shell_allowlist_hook(tool_use, preview=preview)

        assert result is not None
        assert result.action.value == "confirm"

    @pytest.mark.parametrize(
        "preview",
        [
            'echo "hi; bg ls"',
            "echo 'hi; bg ls'",
        ],
    )
    def test_quoted_bg_text_is_not_treated_as_control_syntax(self, preview):
        """A quoted ``bg`` substring is shell data, not a control prefix."""
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content=preview,
        )

        assert shell_allowlist_hook(tool_use, preview=preview) is None

    def test_bg_with_preceding_dangerous_cmd_does_not_auto_confirm(self):
        """When dangerous preceding commands exist, do NOT auto-confirm.

        The full context "cat ~/.ssh/id_rsa\\nbg ls" must NOT be allowlisted
        even though the isolated bg payload ("ls") would be.
        """
        tool_use = ToolUse(
            tool="shell",
            args=[],
            kwargs={},
            content="bg ls",
        )

        # Multi-line preview: preceding dangerous command + bg ls
        result = shell_allowlist_hook(tool_use, preview="cat ~/.ssh/id_rsa\nbg ls")

        # Must NOT auto-confirm — fall through to next hook
        assert result is None, (
            "shell_allowlist_hook must NOT auto-confirm when a dangerous preceding "
            "command is present in the preview"
        )
