"""Tests for subprocess timeout handling in gptme/agent/workspace.py."""

import subprocess
from unittest.mock import patch

import pytest


class TestReplaceTemplateStringsTimeouts:
    """Verify git ls-files in _replace_template_strings handles TimeoutExpired."""

    @patch("subprocess.run")
    def test_git_ls_files_timeout_falls_back_to_manual(self, mock_run, tmp_path):
        """TimeoutExpired on git ls-files falls back to manual traversal."""
        from gptme.agent.workspace import _replace_template_strings

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        # Create a test file with template string
        test_file = tmp_path / "README.md"
        test_file.write_text("Welcome to gptme-agent workspace")

        _replace_template_strings(tmp_path, "my-agent")

        # Should still have replaced strings via manual fallback
        assert "my-agent" in test_file.read_text()

    @patch("subprocess.run")
    def test_git_ls_files_passes_timeout(self, mock_run, tmp_path):
        """git ls-files call includes timeout parameter."""
        from gptme.agent.workspace import _replace_template_strings

        # Return empty file list
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        _replace_template_strings(tmp_path, "my-agent")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 30


class TestResetGitHistoryTimeouts:
    """Verify git init/add/commit in _reset_git_history have timeouts."""

    @patch("subprocess.run")
    def test_git_init_timeout_propagates(self, mock_run, tmp_path):
        """TimeoutExpired on git init propagates as-is."""
        from gptme.agent.workspace import _reset_git_history

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        # _reset_git_history calls shutil.rmtree on .git first
        (tmp_path / ".git").mkdir()

        with pytest.raises(subprocess.TimeoutExpired):
            _reset_git_history(tmp_path, "test-agent")

    @patch("subprocess.run")
    def test_all_git_calls_have_timeout(self, mock_run, tmp_path):
        """git init, add, and commit all include timeout parameters."""
        from gptme.agent.workspace import _reset_git_history

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        (tmp_path / ".git").mkdir()
        _reset_git_history(tmp_path, "test-agent")

        # Should have 3 subprocess calls: init, add, commit
        assert mock_run.call_count == 3
        for i, call in enumerate(mock_run.call_args_list):
            _, kwargs = call
            assert "timeout" in kwargs, f"Call {i} missing timeout"

    @patch("subprocess.run")
    def test_git_init_has_30s_timeout(self, mock_run, tmp_path):
        """git init gets 30s timeout."""
        from gptme.agent.workspace import _reset_git_history

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        (tmp_path / ".git").mkdir()
        _reset_git_history(tmp_path, "test-agent")

        init_call = mock_run.call_args_list[0]
        _, kwargs = init_call
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    def test_git_add_has_60s_timeout(self, mock_run, tmp_path):
        """git add gets 60s timeout (larger workspace possible)."""
        from gptme.agent.workspace import _reset_git_history

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        (tmp_path / ".git").mkdir()
        _reset_git_history(tmp_path, "test-agent")

        add_call = mock_run.call_args_list[1]
        _, kwargs = add_call
        assert kwargs["timeout"] == 60

    @patch("subprocess.run")
    def test_git_commit_has_60s_timeout(self, mock_run, tmp_path):
        """git commit gets 60s timeout."""
        from gptme.agent.workspace import _reset_git_history

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        (tmp_path / ".git").mkdir()
        _reset_git_history(tmp_path, "test-agent")

        commit_call = mock_run.call_args_list[2]
        _, kwargs = commit_call
        assert kwargs["timeout"] == 60


class TestCreateWorkspaceTimeoutIntegration:
    """Verify create_workspace_from_template catches TimeoutExpired from helpers."""

    @patch("subprocess.run")
    def test_workspace_creation_catches_timeout(self, mock_run, tmp_path):
        """create_workspace_from_template wraps TimeoutExpired in WorkspaceError."""
        from gptme.agent.workspace import (
            WorkspaceError,
            create_workspace_from_template,
        )

        # First call (clone) succeeds, second (submodule) times out
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b""),
            subprocess.TimeoutExpired(cmd="git", timeout=300),
        ]

        dest = tmp_path / "new-agent"
        with pytest.raises(WorkspaceError, match="timed out"):
            create_workspace_from_template(dest, "test-agent")

    @patch("gptme.agent.workspace._reset_git_history")
    @patch("gptme.agent.workspace._replace_template_strings")
    @patch("subprocess.run")
    def test_orphaned_workspace_cleaned_up_on_post_move_timeout(
        self, mock_run, mock_replace, mock_reset, tmp_path
    ):
        """Workspace at path is removed if _reset_git_history times out after move."""
        from gptme.agent.workspace import (
            WorkspaceError,
            create_workspace_from_template,
        )

        # clone and submodule succeed (no fork_command → move happens after these)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        mock_replace.return_value = None
        mock_reset.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        dest = tmp_path / "new-agent"
        with pytest.raises(WorkspaceError, match="timed out"):
            create_workspace_from_template(dest, "test-agent")

        # The partially-created workspace must not be left behind
        assert not dest.exists(), "Orphaned workspace was not cleaned up after timeout"

    @patch("gptme.agent.workspace._merge_project_config")
    @patch("gptme.agent.workspace._reset_git_history")
    @patch("gptme.agent.workspace._replace_template_strings")
    @patch("subprocess.run")
    def test_workspace_exists_after_successful_creation(
        self, mock_run, mock_replace, mock_reset, mock_merge, tmp_path
    ):
        """Workspace at path is NOT deleted when creation succeeds (no fork_command)."""
        from gptme.agent.workspace import create_workspace_from_template

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"", stderr=b""
        )
        mock_replace.return_value = None
        mock_reset.return_value = None
        mock_merge.return_value = None

        dest = tmp_path / "new-agent"
        result = create_workspace_from_template(dest, "test-agent")

        assert result == dest
        assert dest.exists(), (
            "Workspace was unexpectedly deleted after successful creation"
        )
