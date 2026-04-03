"""Tests for subprocess timeout handling in misc modules.

Covers: __version__, dirs, cli/wut, context/selector/file_selector.
"""

import importlib
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

# ── __version__.py ───────────────────────────────────────────────────

# gptme/__init__.py does `from .__version__ import __version__`, which means
# `gptme.__version__` resolves to a string attribute, not the module.
# Use importlib to get the actual module object for patch.object.
_version_mod = importlib.import_module("gptme.__version__")


class TestVersionTimeouts:
    """Verify git_cmd and subprocess.call in __version__ have timeouts."""

    def test_git_cmd_passes_timeout(self):
        """git_cmd helper passes timeout to check_output."""
        with (
            patch.object(_version_mod.subprocess, "check_output") as mock_co,
            patch.object(_version_mod.subprocess, "call", return_value=0),
        ):
            mock_co.return_value = "v1.0.0\n"
            from gptme.__version__ import get_git_version

            get_git_version("/tmp")
            for call in mock_co.call_args_list:
                assert "timeout" in call.kwargs, (
                    f"check_output call missing timeout: {call}"
                )

    def test_subprocess_call_passes_timeout(self):
        """subprocess.call for git repo check passes timeout."""
        with (
            patch.object(_version_mod.subprocess, "check_output") as mock_co,
            patch.object(_version_mod.subprocess, "call", return_value=0) as mock_call,
        ):
            mock_co.return_value = "v1.0.0\n"
            from gptme.__version__ import get_git_version

            get_git_version("/tmp")
            assert "timeout" in mock_call.call_args.kwargs, (
                "subprocess.call missing timeout"
            )

    def test_timeout_returns_none(self):
        """TimeoutExpired from git commands returns None gracefully."""
        with patch.object(
            _version_mod.subprocess,
            "call",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
        ):
            from gptme.__version__ import get_git_version

            result = get_git_version("/tmp")
            assert result is None

    def test_check_output_timeout_returns_none(self):
        """TimeoutExpired from check_output returns None gracefully."""
        with (
            patch.object(
                _version_mod.subprocess,
                "check_output",
                side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10),
            ),
            patch.object(_version_mod.subprocess, "call", return_value=0),
        ):
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
    def test_get_workspace_files_timeout_fallback(self, mock_run, tmp_path):
        """TimeoutExpired falls back to glob (returns list, not crash)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
        # Create a file so glob has something to find
        (tmp_path / "example.py").write_text("# test")
        from gptme.context.selector.file_selector import get_workspace_files

        result = get_workspace_files(tmp_path)
        assert isinstance(result, list)
        assert len(result) >= 1

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
