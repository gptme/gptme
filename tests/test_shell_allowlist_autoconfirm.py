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
    # Dangerous patterns with allowlisted commands - should NOT be allowlisted
    ("find . -name '*.py' -exec rm {} \\;", False, "find -exec rm"),
    ("find . -type f -exec cat {} \\;", False, "find -exec cat"),
    (
        "find / -name passwd -exec cat {} \\;",
        False,
        "find -exec to read sensitive files",
    ),
    ("find . -name '*.log' -execdir rm {} \\;", False, "find -execdir"),
    ("find /tmp -type f -delete", False, "find -delete"),
    ("find . -name '*.txt' -ok cat {} \\;", False, "find -ok"),
    ("cat file | xargs rm", False, "pipe to xargs rm"),
    ("grep pattern file | xargs python", False, "pipe to xargs python"),
    ("cat file | sh", False, "pipe to sh"),
    ("head file | bash", False, "pipe to bash"),
    ("ls | zsh", False, "pipe to zsh"),
    ("cat script.sh | fish", False, "pipe to fish"),
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
        """Test that allowlisted commands execute without calling confirmation."""
        cmd = "cat README.md | head -100"

        # Mock execute_with_confirmation to track if it's called
        with patch("gptme.tools.shell.execute_with_confirmation") as mock_confirm:
            # Execute the command - args must be [] not None for code path
            messages = list(execute_shell(cmd, [], None))

            # execute_with_confirmation should NOT be called for allowlisted commands
            mock_confirm.assert_not_called()

            # Should have executed and returned a message
            assert len(messages) == 1
            assert "Ran allowlisted command" in messages[0].content
            assert (
                "cat README.md | head -100" in messages[0].content
                or "cat README.md" in messages[0].content
            )

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
