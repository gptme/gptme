"""Tests for subprocess timeout handling in misc modules.

Covers: __version__, dirs, cli/wut, context/selector/file_selector.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# ── __version__.py ───────────────────────────────────────────────────


class TestVersionTimeouts:
    """Verify git_cmd and subprocess.call in __version__ have timeouts."""

    @patch("gptme.__version__.subprocess.check_output")
    @patch("gptme.__version__.subprocess.call", return_value=0)
    def test_git_cmd_passes_timeout(self, mock_call, mock_check_output):
        """git_cmd helper passes timeout to check_output."""
        mock_check_output.return_value = "v1.0.0\n"
        from gptme.__version__ import get_git_version

        get_git_version("/tmp")
        # check_output should be called with timeout
        for call in mock_check_output.call_args_list:
            assert "timeout" in call.kwargs, (
                f"check_output call missing timeout: {call}"
            )

    @patch("gptme.__version__.subprocess.check_output")
    @patch("gptme.__version__.subprocess.call", return_value=0)
    def test_subprocess_call_passes_timeout(self, mock_call, mock_check_output):
        """subprocess.call for git repo check passes timeout."""
        mock_check_output.return_value = "v1.0.0\n"
        from gptme.__version__ import get_git_version

        get_git_version("/tmp")
        assert "timeout" in mock_call.call_args.kwargs, (
            "subprocess.call missing timeout"
        )

    @patch("gptme.__version__.subprocess.check_output")
    @patch("gptme.__version__.subprocess.call")
    def test_timeout_returns_none(self, mock_call, mock_check_output):
        """TimeoutExpired from git commands returns None gracefully."""
        mock_call.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        from gptme.__version__ import get_git_version

        result = get_git_version("/tmp")
        assert result is None

    @patch("gptme.__version__.subprocess.check_output")
    @patch("gptme.__version__.subprocess.call", return_value=0)
    def test_check_output_timeout_returns_none(self, mock_call, mock_check_output):
        """TimeoutExpired from check_output returns None gracefully."""
        mock_check_output.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        from gptme.__version__ import get_git_version

        result = get_git_version("/tmp")
        assert result is None


# ── dirs.py ──────────────────────────────────────────────────────────


class TestDirsTimeouts:
    """Verify _get_project_git_dir_call has timeout."""

    @patch("gptme.dirs.subprocess.run")
    def test_get_project_git_dir_call_passes_timeout(self, mock_run):
        """_get_project_git_dir_call passes timeout to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="/tmp/repo\n"
        )
        from gptme.dirs import _get_project_git_dir_call

        _get_project_git_dir_call()
        assert "timeout" in mock_run.call_args.kwargs

    @patch("gptme.dirs.subprocess.run")
    def test_get_project_git_dir_call_timeout_returns_none(self, mock_run):
        """TimeoutExpired returns None gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=10)
        from gptme.dirs import _get_project_git_dir_call

        result = _get_project_git_dir_call()
        assert result is None


# ── cli/wut.py ───────────────────────────────────────────────────────


class TestWutTimeouts:
    """Verify tmux capture and gptme launch have timeouts."""

    @patch.dict("os.environ", {"TMUX": "/tmp/tmux", "TMUX_PANE": "%0"})
    @patch("gptme.cli.wut.subprocess.run")
    def test_get_tmux_content_passes_timeout(self, mock_run):
        """get_tmux_content passes timeout to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="pane content\n"
        )
        from gptme.cli.wut import get_tmux_content

        get_tmux_content()
        assert "timeout" in mock_run.call_args.kwargs

    @patch.dict("os.environ", {"TMUX": "/tmp/tmux", "TMUX_PANE": "%0"})
    @patch("gptme.cli.wut.subprocess.run")
    def test_get_tmux_content_timeout_raises(self, mock_run):
        """TimeoutExpired from tmux capture propagates."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tmux", timeout=10)
        from gptme.cli.wut import get_tmux_content

        with pytest.raises(subprocess.TimeoutExpired):
            get_tmux_content()


# ── context/selector/file_selector.py ────────────────────────────────


class TestFileSelectorTimeouts:
    """Verify git ls-files and git status have timeouts."""

    @patch("gptme.context.selector.file_selector.subprocess.run")
    def test_get_workspace_files_passes_timeout(self, mock_run):
        """get_workspace_files passes timeout to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="file1.py\nfile2.py\n"
        )
        from gptme.context.selector.file_selector import get_workspace_files

        get_workspace_files(Path("/tmp"))
        assert "timeout" in mock_run.call_args.kwargs

    @patch("gptme.context.selector.file_selector.subprocess.run")
    def test_get_workspace_files_timeout_fallback(self, mock_run):
        """TimeoutExpired falls back to glob (returns list, not crash)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        from gptme.context.selector.file_selector import get_workspace_files

        result = get_workspace_files(Path("/tmp"))
        assert isinstance(result, list)

    @patch("gptme.context.selector.file_selector.subprocess.run")
    def test_get_git_status_files_passes_timeout(self, mock_run):
        """get_git_status_files passes timeout to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=" M file1.py\n?? file2.py\n"
        )
        from gptme.context.selector.file_selector import get_git_status_files

        get_git_status_files(Path("/tmp"))
        assert "timeout" in mock_run.call_args.kwargs

    @patch("gptme.context.selector.file_selector.subprocess.run")
    def test_get_git_status_files_timeout_returns_empty(self, mock_run):
        """TimeoutExpired returns empty dict gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        from gptme.context.selector.file_selector import get_git_status_files

        result = get_git_status_files(Path("/tmp"))
        assert result == {}
