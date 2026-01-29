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
from gptme.agent.workspace import (
    DetectedWorkspace,
    detect_workspaces,
    get_workspace_name,
    is_agent_workspace,
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

    def test_create_help(self, runner):
        """Test create command help."""
        result = runner.invoke(main, ["create", "--help"])
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

    def test_create_creates_workspace(self, runner, tmp_path):
        """Test create command creates workspace structure (minimal mode)."""
        workspace = tmp_path / "test-agent"

        # Use --no-template to test minimal create without network dependency
        result = runner.invoke(main, ["create", "--no-template", str(workspace)])
        assert result.exit_code == 0, f"Create failed: {result.output}"
        assert "Workspace created" in result.output

        # Verify structure created
        assert workspace.exists()
        assert (workspace / "journal").is_dir()
        assert (workspace / "tasks").is_dir()
        assert (workspace / "knowledge").is_dir()
        assert (workspace / "lessons").is_dir()  # Also created in minimal mode
        assert (workspace / "people").is_dir()  # Also created in minimal mode
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

    def test_uninstall_help(self, runner):
        """Test uninstall command help."""
        result = runner.invoke(main, ["uninstall", "--help"])
        assert result.exit_code == 0
        assert "Uninstall an agent's services" in result.output

    def test_status_shows_detected_workspaces(self, runner, tmp_path):
        """Test that status --all shows detected workspaces."""
        # Create a fake workspace
        workspace = tmp_path / "test-agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "test-agent"\n')

        with (
            patch("gptme.agent.cli.get_service_manager") as mock_get,
            patch("gptme.agent.cli.detect_workspaces") as mock_detect,
        ):
            mock_manager = mock_get.return_value
            mock_manager.list_agents.return_value = []

            mock_detect.return_value = [
                DetectedWorkspace(path=workspace, name="test-agent", installed=False)
            ]

            result = runner.invoke(main, ["status", "--all"])
            assert result.exit_code == 0
            assert "test-agent" in result.output
            assert "Detected workspaces" in result.output

    def test_status_specific_agent_not_installed(self, runner, tmp_path):
        """Test status for specific agent that is detected but not installed."""
        workspace = tmp_path / "my-agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "my-agent"\n')

        with (
            patch("gptme.agent.cli.get_service_manager") as mock_get,
            patch("gptme.agent.cli.detect_workspaces") as mock_detect,
        ):
            mock_manager = mock_get.return_value
            mock_manager.list_agents.return_value = []
            mock_manager.status.return_value = None

            mock_detect.return_value = [
                DetectedWorkspace(path=workspace, name="my-agent", installed=False)
            ]

            result = runner.invoke(main, ["status", "my-agent"])
            assert result.exit_code == 0
            assert "my-agent" in result.output
            assert "To install:" in result.output

    def test_uninstall_agent(self, runner):
        """Test uninstall command calls service manager."""
        with patch("gptme.agent.cli.get_service_manager") as mock_get:
            mock_manager = mock_get.return_value
            mock_manager.uninstall.return_value = True

            result = runner.invoke(main, ["uninstall", "test-agent", "--yes"])
            assert result.exit_code == 0
            assert "uninstalled" in result.output
            mock_manager.uninstall.assert_called_once_with("test-agent")

    def test_uninstall_agent_fails(self, runner):
        """Test uninstall command handles failure."""
        with patch("gptme.agent.cli.get_service_manager") as mock_get:
            mock_manager = mock_get.return_value
            mock_manager.uninstall.return_value = False

            result = runner.invoke(main, ["uninstall", "test-agent", "--yes"])
            assert result.exit_code == 1
            assert "Failed to uninstall" in result.output

    def test_install_detects_existing_workspace(self, runner, tmp_path):
        """Test install command validates workspace has run script."""
        workspace = tmp_path / "incomplete-agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "incomplete"\n')
        # No scripts/runs/autonomous/autonomous-run.sh

        result = runner.invoke(
            main, ["install", "--workspace", str(workspace)], catch_exceptions=False
        )
        assert result.exit_code == 1
        assert "No autonomous run script found" in result.output


class TestWorkspaceDetection:
    """Tests for workspace detection functions."""

    def test_is_agent_workspace_valid(self, tmp_path):
        """Test detection of valid agent workspace."""
        workspace = tmp_path / "agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "test"\n')

        assert is_agent_workspace(workspace) is True

    def test_is_agent_workspace_no_config(self, tmp_path):
        """Test detection with missing config."""
        workspace = tmp_path / "agent"
        workspace.mkdir()

        assert is_agent_workspace(workspace) is False

    def test_is_agent_workspace_no_agent_section(self, tmp_path):
        """Test detection with config but no agent section."""
        workspace = tmp_path / "agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('files = ["README.md"]\n')

        assert is_agent_workspace(workspace) is False

    def test_get_workspace_name_valid(self, tmp_path):
        """Test getting name from valid workspace."""
        workspace = tmp_path / "agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "bob"\n')

        assert get_workspace_name(workspace) == "bob"

    def test_get_workspace_name_invalid(self, tmp_path):
        """Test getting name from invalid workspace."""
        workspace = tmp_path / "agent"
        workspace.mkdir()

        assert get_workspace_name(workspace) is None

    def test_detect_workspaces_finds_agents(self, tmp_path):
        """Test that detect_workspaces finds agent directories."""
        # Create test workspaces
        agent1 = tmp_path / "agent1"
        agent1.mkdir()
        (agent1 / "gptme.toml").write_text('[agent]\nname = "agent1"\n')

        agent2 = tmp_path / "subdir" / "agent2"
        agent2.mkdir(parents=True)
        (agent2 / "gptme.toml").write_text('[agent]\nname = "agent2"\n')

        # Non-agent directory
        other = tmp_path / "other"
        other.mkdir()
        (other / "gptme.toml").write_text('files = ["README.md"]\n')

        # Detect
        workspaces = detect_workspaces(search_paths=[tmp_path])
        names = {ws.name for ws in workspaces}

        assert "agent1" in names
        # agent2 is nested deeper than 1 level from tmp_path
        # detect_workspaces only checks 1 level deep from search paths
        # But tmp_path/subdir is checked, so agent2 should be found
        assert "agent2" not in names  # It's 2 levels deep

    def test_detect_workspaces_marks_installed(self, tmp_path):
        """Test that installed agents are marked correctly."""
        agent = tmp_path / "my-agent"
        agent.mkdir()
        (agent / "gptme.toml").write_text('[agent]\nname = "my-agent"\n')

        # Detect with my-agent marked as installed
        workspaces = detect_workspaces(
            search_paths=[tmp_path], installed_agents=["my-agent"]
        )

        assert len(workspaces) == 1
        assert workspaces[0].name == "my-agent"
        assert workspaces[0].installed is True

    def test_detected_workspace_has_run_script(self, tmp_path):
        """Test has_run_script property."""
        workspace = tmp_path / "agent"
        workspace.mkdir()
        (workspace / "gptme.toml").write_text('[agent]\nname = "agent"\n')

        detected = DetectedWorkspace(path=workspace, name="agent", installed=False)
        assert detected.has_run_script is False

        # Add run script
        scripts_dir = workspace / "scripts" / "runs" / "autonomous"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "autonomous-run.sh").write_text("#!/bin/bash\n")

        assert detected.has_run_script is True

    def test_detect_workspaces_no_duplicates(self, tmp_path):
        """Test that same agent isn't detected twice from different paths."""
        agent = tmp_path / "agent"
        agent.mkdir()
        (agent / "gptme.toml").write_text('[agent]\nname = "unique-agent"\n')

        # Search same path twice
        workspaces = detect_workspaces(search_paths=[tmp_path, tmp_path])
        names = [ws.name for ws in workspaces]

        assert names.count("unique-agent") == 1
