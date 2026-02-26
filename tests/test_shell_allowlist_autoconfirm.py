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


class TestIsAllowlisted:
    """Tests for the is_allowlisted function."""

    def test_cat_with_pipe_and_head(self):
        """Test that cat with pipe to head is allowlisted."""
        cmd = "cat gptme/cli/commands.py | head -100"
        assert is_allowlisted(cmd) is True

    def test_simple_cat(self):
        """Test that simple cat is allowlisted."""
        cmd = "cat README.md"
        assert is_allowlisted(cmd) is True

    def test_simple_head(self):
        """Test that simple head is allowlisted."""
        cmd = "head -100 file.txt"
        assert is_allowlisted(cmd) is True

    def test_grep_sort_head_pipeline(self):
        """Test that a pipeline of allowlisted commands is allowlisted."""
        cmd = "grep pattern file | sort | head -10"
        assert is_allowlisted(cmd) is True

    def test_cat_with_redirection_not_allowlisted(self):
        """Test that cat with output redirection is NOT allowlisted."""
        cmd = "cat file > output.txt"
        assert is_allowlisted(cmd) is False

    def test_echo_with_redirection_not_allowlisted(self):
        """Test that echo with redirection is NOT allowlisted."""
        cmd = "echo 'hello' > output.txt"
        assert is_allowlisted(cmd) is False

    def test_non_allowlisted_command(self):
        """Test that non-allowlisted commands are not allowlisted."""
        cmd = "rm -rf /tmp/foo"
        assert is_allowlisted(cmd) is False

    def test_pipe_with_non_allowlisted_command(self):
        """Test that a pipe to a non-allowlisted command is not allowlisted."""
        cmd = "cat file | xargs rm"
        assert is_allowlisted(cmd) is False

    def test_ls_variants(self):
        """Test that ls variants are allowlisted."""
        assert is_allowlisted("ls") is True
        assert is_allowlisted("ls -la") is True
        assert is_allowlisted("ls -la /tmp") is True

    def test_pwd(self):
        """Test that pwd is allowlisted."""
        assert is_allowlisted("pwd") is True

    def test_rg_ripgrep(self):
        """Test that rg (ripgrep) is allowlisted."""
        assert is_allowlisted("rg pattern") is True
        assert is_allowlisted("rg pattern file.txt") is True

    def test_find(self):
        """Test that find is allowlisted."""
        assert is_allowlisted("find . -name '*.py'") is True

    def test_tree(self):
        """Test that tree is allowlisted."""
        assert is_allowlisted("tree -L 2") is True


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
