"""Tests for gptme.agent module."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.agent.cli import main
from gptme.agent.service import (
    LaunchdManager,
    ServiceStatus,
    SystemdManager,
    detect_service_manager,
)


class TestDetectServiceManager:
    """Tests for service manager detection."""

    def test_detect_linux_systemd(self):
        """Test detection on Linux with systemd."""
        with patch("platform.system", return_value="Linux"):
            with patch("pathlib.Path.exists", return_value=True):
                result = detect_service_manager()
                assert result == "systemd"

    def test_detect_linux_no_systemd(self):
        """Test detection on Linux without systemd."""
        with patch("platform.system", return_value="Linux"):
            with patch("pathlib.Path.exists", return_value=False):
                result = detect_service_manager()
                assert result == "none"

    def test_detect_macos(self):
        """Test detection on macOS."""
        with patch("platform.system", return_value="Darwin"):
            result = detect_service_manager()
            assert result == "launchd"

    def test_detect_windows(self):
        """Test detection on Windows (unsupported)."""
        with patch("platform.system", return_value="Windows"):
            result = detect_service_manager()
            assert result == "none"


class TestServiceStatus:
    """Tests for ServiceStatus dataclass."""

    def test_service_status_creation(self):
        """Test creating a ServiceStatus."""
        status = ServiceStatus(
            name="test-agent",
            running=True,
            enabled=True,
            pid=1234,
        )
        assert status.name == "test-agent"
        assert status.running is True
        assert status.enabled is True
        assert status.pid == 1234
        assert status.uptime is None


class TestSystemdManager:
    """Tests for SystemdManager."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a SystemdManager with temporary directory."""
        manager = SystemdManager()
        manager.user_dir = tmp_path / "systemd" / "user"
        manager.user_dir.mkdir(parents=True)
        return manager

    def test_service_path(self, manager):
        """Test service path generation."""
        path = manager._service_path("test")
        assert path.name == "gptme-agent-test.service"

    def test_timer_path(self, manager):
        """Test timer path generation."""
        path = manager._timer_path("test")
        assert path.name == "gptme-agent-test.timer"

    def test_list_agents_empty(self, manager):
        """Test listing agents when none installed."""
        agents = manager.list_agents()
        assert agents == []

    def test_list_agents_with_services(self, manager):
        """Test listing agents with installed services."""
        # Create fake service files
        (manager.user_dir / "gptme-agent-alice.service").touch()
        (manager.user_dir / "gptme-agent-bob.service").touch()
        (manager.user_dir / "other.service").touch()

        agents = manager.list_agents()
        assert set(agents) == {"alice", "bob"}


class TestLaunchdManager:
    """Tests for LaunchdManager."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a LaunchdManager with temporary directory."""
        manager = LaunchdManager()
        manager.agents_dir = tmp_path / "LaunchAgents"
        manager.agents_dir.mkdir(parents=True)
        manager.logs_dir = tmp_path / "Logs" / "gptme"
        manager.logs_dir.mkdir(parents=True)
        return manager

    def test_plist_path(self, manager):
        """Test plist path generation."""
        path = manager._plist_path("test")
        assert path.name == "org.gptme.agent.test.plist"

    def test_list_agents_empty(self, manager):
        """Test listing agents when none installed."""
        agents = manager.list_agents()
        assert agents == []

    def test_list_agents_with_plists(self, manager):
        """Test listing agents with installed plists."""
        # Create fake plist files
        (manager.agents_dir / "org.gptme.agent.alice.plist").touch()
        (manager.agents_dir / "org.gptme.agent.bob.plist").touch()
        (manager.agents_dir / "other.plist").touch()

        agents = manager.list_agents()
        assert set(agents) == {"alice", "bob"}


class TestCLI:
    """Tests for CLI commands."""

    @pytest.fixture
    def runner(self):
        """Create a CLI runner."""
        return CliRunner()

    def test_help(self, runner):
        """Test --help option."""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Manage gptme autonomous agents" in result.output

    def test_status_help(self, runner):
        """Test status command help."""
        result = runner.invoke(main, ["status", "--help"])
        assert result.exit_code == 0
        assert "Show status of agent(s)" in result.output

    def test_setup_help(self, runner):
        """Test setup command help."""
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "Set up a new agent workspace" in result.output

    def test_install_help(self, runner):
        """Test install command help."""
        result = runner.invoke(main, ["install", "--help"])
        assert result.exit_code == 0
        assert "Install agent services" in result.output

    def test_logs_help(self, runner):
        """Test logs command help."""
        result = runner.invoke(main, ["logs", "--help"])
        assert result.exit_code == 0
        assert "View agent logs" in result.output

    def test_setup_creates_workspace(self, runner, tmp_path):
        """Test setup command creates workspace structure."""
        workspace = tmp_path / "test-agent"

        result = runner.invoke(main, ["setup", str(workspace)])
        assert result.exit_code == 0
        assert "Workspace setup complete" in result.output

        # Verify structure created
        assert workspace.exists()
        assert (workspace / "journal").is_dir()
        assert (workspace / "tasks").is_dir()
        assert (workspace / "knowledge").is_dir()
        assert (workspace / "gptme.toml").is_file()
        assert (workspace / "README.md").is_file()
        assert (
            workspace / "scripts" / "runs" / "autonomous" / "autonomous-run.sh"
        ).is_file()

    def test_list_no_agents(self, runner):
        """Test list command with no agents."""
        with patch("gptme.agent.cli.get_service_manager") as mock_get:
            mock_manager = mock_get.return_value
            mock_manager.list_agents.return_value = []

            result = runner.invoke(main, ["list"])
            assert result.exit_code == 0
            assert "No agents installed" in result.output
