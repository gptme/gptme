"""Tests for the autocommit tool — automatic commit hints after message processing.

Tests cover:
- autocommit(): git status/diff integration and message generation
- handle_commit_command(): /commit command handler
- autocommit_on_message_complete(): hook function with config and modification checks
- tool spec: registration, hooks, commands
"""

import subprocess
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.autocommit import (
    autocommit,
    autocommit_on_message_complete,
    handle_commit_command,
    tool,
)

# ── autocommit() ────────────────────────────────────────────────────────


class TestAutocommit:
    """Tests for the autocommit() function — git integration and message generation."""

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_returns_commit_prompt_when_changes_exist(self, mock_run: MagicMock):
        """When git status shows changes, returns a message with status + diff."""
        mock_run.side_effect = [
            # git status --porcelain
            MagicMock(stdout=" M file.py\n", returncode=0),
            # git status
            MagicMock(stdout="modified:   file.py\n", returncode=0),
            # git diff HEAD
            MagicMock(stdout="+new line\n-old line\n", returncode=0),
        ]

        result = autocommit()

        assert isinstance(result, Message)
        assert result.role == "system"
        assert "modified:   file.py" in result.content
        assert "+new line" in result.content
        assert "git add" in result.content
        assert "git commit" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_returns_no_changes_message_when_clean(self, mock_run: MagicMock):
        """When working tree is clean, returns 'No changes to commit'."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        result = autocommit()

        assert isinstance(result, Message)
        assert result.role == "system"
        assert "No changes to commit" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_returns_no_changes_for_whitespace_only_status(self, mock_run: MagicMock):
        """Whitespace-only porcelain output is treated as no changes."""
        mock_run.return_value = MagicMock(stdout="   \n  \n", returncode=0)

        result = autocommit()

        assert "No changes to commit" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_handles_git_failure(self, mock_run: MagicMock):
        """When git fails, returns an error message instead of crashing."""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128, cmd=["git", "status"], stderr="fatal: not a git repo"
        )

        result = autocommit()

        assert isinstance(result, Message)
        assert result.role == "system"
        assert "Git operation failed" in result.content
        assert "128" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_git_failure_includes_stderr(self, mock_run: MagicMock):
        """Error message includes stderr from failed git command."""
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["git"], stderr="error: permission denied"
        )

        result = autocommit()

        assert "permission denied" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_git_failure_falls_back_to_stdout(self, mock_run: MagicMock):
        """When stderr is empty, error message falls back to stdout."""
        err = subprocess.CalledProcessError(returncode=1, cmd=["git"], stderr="")
        err.stdout = "some output"
        mock_run.side_effect = err

        result = autocommit()

        assert "some output" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_git_failure_falls_back_to_str(self, mock_run: MagicMock):
        """When both stderr and stdout are empty, falls back to str(e)."""
        err = subprocess.CalledProcessError(returncode=1, cmd=["git"], stderr="")
        err.stdout = ""
        mock_run.side_effect = err

        result = autocommit()

        assert "Git operation failed" in result.content
        # Verify the str(e) fallback content appears, not just the static prefix
        assert str(err) in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_keyboard_interrupt_propagated(self, mock_run: MagicMock):
        """CalledProcessError with returncode -2 is converted to KeyboardInterrupt."""
        mock_run.side_effect = subprocess.CalledProcessError(returncode=-2, cmd=["git"])

        with pytest.raises(KeyboardInterrupt):
            autocommit()

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_unexpected_exception_caught(self, mock_run: MagicMock):
        """Non-subprocess exceptions are caught and returned as error messages."""
        mock_run.side_effect = OSError("disk full")

        result = autocommit()

        assert isinstance(result, Message)
        assert "Autocommit failed" in result.content
        assert "disk full" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_porcelain_called_with_no_untracked(self, mock_run: MagicMock):
        """git status --porcelain uses --untracked-files=no to ignore new files."""
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        autocommit()

        first_call = mock_run.call_args_list[0]
        cmd = first_call[0][0]
        assert "--untracked-files=no" in cmd

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_commit_prompt_mentions_heredoc(self, mock_run: MagicMock):
        """The commit prompt instructs the LLM to use HEREDOC format."""
        mock_run.side_effect = [
            MagicMock(stdout=" M x.py\n", returncode=0),
            MagicMock(stdout="modified: x.py\n", returncode=0),
            MagicMock(stdout="diff\n", returncode=0),
        ]

        result = autocommit()

        assert "HEREDOC" in result.content
        assert "EOF" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_commit_prompt_warns_against_git_add_all(self, mock_run: MagicMock):
        """The prompt warns against using 'git add .' or 'git add -A'."""
        mock_run.side_effect = [
            MagicMock(stdout=" M x.py\n", returncode=0),
            MagicMock(stdout="modified: x.py\n", returncode=0),
            MagicMock(stdout="diff\n", returncode=0),
        ]

        result = autocommit()

        assert "git add ." in result.content or "git add -A" in result.content

    @patch("gptme.tools.autocommit.subprocess.run")
    def test_three_git_commands_called(self, mock_run: MagicMock):
        """When changes exist, exactly 3 git commands are run: porcelain, status, diff."""
        mock_run.side_effect = [
            MagicMock(stdout=" M a.py\n", returncode=0),
            MagicMock(stdout="status output", returncode=0),
            MagicMock(stdout="diff output", returncode=0),
        ]

        autocommit()

        assert mock_run.call_count == 3
        cmds = [call[0][0] for call in mock_run.call_args_list]
        assert "git" in cmds[0]
        assert "--porcelain" in cmds[0]
        assert cmds[1] == ["git", "status"]
        assert cmds[2] == ["git", "diff", "HEAD"]


# ── handle_commit_command() ──────────────────────────────────────────────


class TestHandleCommitCommand:
    """Tests for the /commit command handler."""

    @patch("gptme.tools.autocommit.autocommit")
    def test_yields_autocommit_message(self, mock_ac: MagicMock):
        """The command yields the autocommit message."""
        mock_ac.return_value = Message("system", "commit prompt")
        ctx = MagicMock()

        results = list(handle_commit_command(ctx))

        assert len(results) == 1
        assert results[0].content == "commit prompt"

    @patch("gptme.tools.autocommit.autocommit")
    def test_undoes_command_message(self, mock_ac: MagicMock):
        """The command undoes the /commit message from the log."""
        mock_ac.return_value = Message("system", "prompt")
        ctx = MagicMock()

        list(handle_commit_command(ctx))

        ctx.manager.undo.assert_called_once_with(1, quiet=True)

    @patch("gptme.tools.autocommit.autocommit")
    def test_returns_generator(self, mock_ac: MagicMock):
        """handle_commit_command returns a generator."""
        mock_ac.return_value = Message("system", "prompt")
        ctx = MagicMock()

        result = handle_commit_command(ctx)

        assert isinstance(result, Generator)


# ── autocommit_on_message_complete() ─────────────────────────────────────


class TestAutocommitOnMessageComplete:
    """Tests for the hook function that auto-commits after message processing."""

    @patch("gptme.tools.autocommit.autocommit")
    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_yields_autocommit_when_enabled_and_modified(
        self, mock_config: MagicMock, mock_check: MagicMock, mock_ac: MagicMock
    ):
        """When autocommit is enabled and modifications exist, yields the prompt."""
        mock_config.return_value.get_env_bool.return_value = True
        mock_check.return_value = True
        mock_ac.return_value = Message("system", "commit prompt")

        manager = MagicMock()
        results = list(autocommit_on_message_complete(manager))

        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert results[0].content == "commit prompt"

    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_skips_when_autocommit_disabled(
        self, mock_config: MagicMock, mock_check: MagicMock
    ):
        """When GPTME_AUTOCOMMIT is not set, yields nothing."""
        mock_config.return_value.get_env_bool.return_value = None

        manager = MagicMock()
        results = list(autocommit_on_message_complete(manager))

        assert len(results) == 0
        mock_check.assert_not_called()

    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_skips_when_autocommit_false(
        self, mock_config: MagicMock, mock_check: MagicMock
    ):
        """When GPTME_AUTOCOMMIT=false, yields nothing."""
        mock_config.return_value.get_env_bool.return_value = False

        manager = MagicMock()
        results = list(autocommit_on_message_complete(manager))

        assert len(results) == 0

    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_skips_when_no_modifications(
        self, mock_config: MagicMock, mock_check: MagicMock
    ):
        """When enabled but no file modifications detected, yields nothing."""
        mock_config.return_value.get_env_bool.return_value = True
        mock_check.return_value = False

        manager = MagicMock()
        results = list(autocommit_on_message_complete(manager))

        assert len(results) == 0

    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_passes_log_to_check_for_modifications(
        self, mock_config: MagicMock, mock_check: MagicMock
    ):
        """check_for_modifications is called with the manager's log."""
        mock_config.return_value.get_env_bool.return_value = True
        mock_check.return_value = False

        manager = MagicMock()
        manager.log = ["msg1", "msg2"]
        list(autocommit_on_message_complete(manager))

        mock_check.assert_called_once_with(["msg1", "msg2"])

    @pytest.mark.parametrize(
        "exc",
        [RuntimeError("something broke"), ValueError("bad value")],
        ids=["RuntimeError", "ValueError"],
    )
    @patch("gptme.tools.autocommit.autocommit")
    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_catches_exception_in_autocommit(
        self,
        mock_config: MagicMock,
        mock_check: MagicMock,
        mock_ac: MagicMock,
        exc: Exception,
    ):
        """If autocommit() raises any Exception subclass, the hook catches it,
        yields a hidden error message, and includes 'Autocommit failed'."""
        mock_config.return_value.get_env_bool.return_value = True
        mock_check.return_value = True
        mock_ac.side_effect = exc

        manager = MagicMock()
        results = list(autocommit_on_message_complete(manager))

        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert "Autocommit failed" in results[0].content
        assert results[0].hide is True

    @patch("gptme.tools.autocommit.check_for_modifications")
    @patch("gptme.tools.autocommit.get_config")
    def test_returns_generator(self, mock_config: MagicMock, mock_check: MagicMock):
        """autocommit_on_message_complete returns a generator."""
        mock_config.return_value.get_env_bool.return_value = False

        manager = MagicMock()
        result = autocommit_on_message_complete(manager)

        assert isinstance(result, Generator)


# ── tool spec ────────────────────────────────────────────────────────────


class TestToolSpec:
    """Tests for the autocommit tool specification."""

    def test_tool_name(self):
        assert tool.name == "autocommit"

    def test_tool_has_description(self):
        assert tool.desc is not None
        assert len(tool.desc) > 0

    def test_tool_has_instructions(self):
        assert tool.instructions is not None
        assert "commit" in tool.instructions.lower()

    def test_tool_is_available(self):
        assert tool.available is True

    def test_tool_has_hook(self):
        assert "autocommit" in tool.hooks
        hook_name, hook_fn, hook_priority = tool.hooks["autocommit"]
        assert hook_name == "turn.post"
        assert callable(hook_fn)

    def test_hook_has_low_priority(self):
        """Autocommit hook runs with low priority (after pre-commit checks)."""
        _, _, priority = tool.hooks["autocommit"]
        assert priority == 1

    def test_hook_function_is_autocommit_handler(self):
        """The hook function points to autocommit_on_message_complete."""
        _, hook_fn, _ = tool.hooks["autocommit"]
        assert hook_fn is autocommit_on_message_complete

    def test_tool_has_commit_command(self):
        assert "commit" in tool.commands

    def test_commit_command_is_handler(self):
        assert tool.commands["commit"] is handle_commit_command

    def test_tool_has_no_examples(self):
        """Autocommit is a hook tool — it doesn't need user-facing examples."""
        assert not tool.examples

    def test_tool_has_no_block_types(self):
        """Autocommit doesn't define block types (no tool_use blocks)."""
        assert not tool.block_types
