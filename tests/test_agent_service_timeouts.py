"""Tests for subprocess timeout handling in gptme/agent/service.py."""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.agent.service import LaunchdManager, SystemdManager


class TestSystemdManagerTimeouts:
    """Verify systemctl and journalctl calls handle TimeoutExpired."""

    @patch("subprocess.run")
    def test_run_systemctl_timeout_returns_failed_result(self, mock_run):
        """TimeoutExpired on systemctl returns CompletedProcess with rc=1."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="systemctl", timeout=30)
        mgr = SystemdManager.__new__(SystemdManager)
        result = mgr._run_systemctl("status", "gptme-agent-test.service")
        assert result.returncode == 1
        assert result.stdout == ""

    @patch("subprocess.run")
    def test_run_systemctl_passes_timeout(self, mock_run):
        """All systemctl calls include a timeout parameter."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mgr = SystemdManager.__new__(SystemdManager)
        mgr._run_systemctl("daemon-reload")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    def test_logs_timeout_returns_error_message(self, mock_run):
        """TimeoutExpired on journalctl returns error string."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="journalctl", timeout=30)
        mgr = SystemdManager.__new__(SystemdManager)
        result = mgr.logs("test-agent", lines=50)
        assert "timed out" in result

    @patch("subprocess.run")
    def test_logs_passes_timeout(self, mock_run):
        """journalctl call includes timeout parameter."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="log lines here", stderr=""
        )
        mgr = SystemdManager.__new__(SystemdManager)
        result = mgr.logs("test-agent", lines=50)
        assert result == "log lines here"
        _, kwargs = mock_run.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    def test_status_handles_systemctl_timeout(self, mock_run):
        """status() gracefully handles systemctl timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="systemctl", timeout=30)
        mgr = SystemdManager.__new__(SystemdManager)
        # status() calls _run_systemctl which catches TimeoutExpired
        # and returns rc=1, so status should return None
        result = mgr.status("test-agent")
        assert result is None


class TestLaunchdManagerTimeouts:
    """Verify launchctl and tail calls handle TimeoutExpired."""

    @patch("subprocess.run")
    def test_run_launchctl_timeout_returns_failed_result(self, mock_run):
        """TimeoutExpired on launchctl returns CompletedProcess with rc=1."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="launchctl", timeout=30)
        mgr = LaunchdManager.__new__(LaunchdManager)
        result = mgr._run_launchctl("list", "org.gptme.agent.test")
        assert result.returncode == 1
        assert result.stdout == ""

    @patch("subprocess.run")
    def test_run_launchctl_passes_timeout(self, mock_run):
        """All launchctl calls include a timeout parameter."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mgr = LaunchdManager.__new__(LaunchdManager)
        mgr._run_launchctl("list")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == 30

    @patch("subprocess.run")
    def test_logs_timeout_returns_error_message(self, mock_run):
        """TimeoutExpired on tail returns error string."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="tail", timeout=10)
        mgr = LaunchdManager.__new__(LaunchdManager)
        # Create a temp file to simulate a log file
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            f.write(b"test log")
            log_path = Path(f.name)
        try:
            # Patch _log_path to return the temp file
            with patch.object(type(mgr), "_log_path", return_value=log_path):
                result = mgr.logs("test-agent", lines=50)
            assert "timed out" in result
        finally:
            log_path.unlink()

    @patch("subprocess.run")
    def test_logs_passes_timeout(self, mock_run):
        """tail call includes timeout parameter."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="log output", stderr=""
        )
        mgr = LaunchdManager.__new__(LaunchdManager)
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            f.write(b"test log")
            log_path = Path(f.name)
        try:
            with patch.object(type(mgr), "_log_path", return_value=log_path):
                result = mgr.logs("test-agent", lines=50)
            assert result == "log output"
            _, kwargs = mock_run.call_args
            assert "timeout" in kwargs
            assert kwargs["timeout"] == 10
        finally:
            log_path.unlink()


class TestTimeoutCoverage:
    """Verify all subprocess calls in service.py pass a timeout."""

    @pytest.mark.parametrize(
        ("method", "args"),
        [
            ("_run_systemctl", ("daemon-reload",)),
            ("logs", ("test",)),
        ],
    )
    @patch("subprocess.run")
    def test_systemd_all_calls_have_timeout(self, mock_run, method, args):
        """Every subprocess.run call in SystemdManager includes timeout."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        mgr = SystemdManager.__new__(SystemdManager)
        getattr(mgr, method)(*args)
        for call in mock_run.call_args_list:
            _, kwargs = call
            assert "timeout" in kwargs, f"{method} missing timeout"
