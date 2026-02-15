"""Tests for gptme.agent module."""

import plistlib
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gptme.agent.cli import main
from gptme.agent.service import (
    LaunchdManager,
    ServiceStatus,
    SystemdManager,
    _build_launchd_plist,
    detect_service_manager,
    parse_schedule,
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


class TestParseSchedule:
    """Tests for schedule parsing (systemd -> launchd conversion)."""

    def test_interval_every_30_minutes(self):
        result = parse_schedule("*:00/30")
        assert result == {"StartInterval": 1800}

    def test_interval_every_15_minutes(self):
        result = parse_schedule("*:00/15")
        assert result == {"StartInterval": 900}

    def test_hourly(self):
        result = parse_schedule("*:00")
        assert result == {"StartCalendarInterval": [{"Minute": 0}]}

    def test_hourly_at_minute_30(self):
        result = parse_schedule("*:30")
        assert result == {"StartCalendarInterval": [{"Minute": 30}]}

    def test_daily_at_time(self):
        result = parse_schedule("*-*-* 06:00")
        assert result == {"StartCalendarInterval": [{"Hour": 6, "Minute": 0}]}

    def test_daily_at_specific_time(self):
        result = parse_schedule("*-*-* 14:30")
        assert result == {"StartCalendarInterval": [{"Hour": 14, "Minute": 30}]}

    def test_weekday_hourly(self):
        result = parse_schedule("Mon *:00")
        assert result == {"StartCalendarInterval": [{"Weekday": 1, "Minute": 0}]}

    def test_weekday_at_time(self):
        result = parse_schedule("Fri 09:00")
        assert result == {
            "StartCalendarInterval": [{"Weekday": 5, "Hour": 9, "Minute": 0}]
        }

    def test_unrecognized_defaults_to_hourly(self):
        result = parse_schedule("some-weird-format")
        assert result == {"StartCalendarInterval": [{"Minute": 0}]}

    def test_whitespace_stripped(self):
        result = parse_schedule("  *:00/30  ")
        assert result == {"StartInterval": 1800}


class TestBuildLaunchdPlist:
    """Tests for plist generation using plistlib."""

    def test_basic_plist_structure(self, tmp_path):
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist("test", workspace, log_path)
        plist = plistlib.loads(plist_bytes)

        assert plist["Label"] == "org.gptme.agent.test"
        assert plist["WorkingDirectory"] == str(workspace)
        assert plist["StandardOutPath"] == str(log_path)
        assert plist["StandardErrorPath"] == str(log_path)
        assert plist["RunAtLoad"] is False
        assert (
            str(workspace / "scripts" / "runs" / "autonomous" / "autonomous-run.sh")
            in plist["ProgramArguments"]
        )

    def test_plist_with_interval_schedule(self, tmp_path):
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist(
            "test", workspace, log_path, schedule="*:00/30"
        )
        plist = plistlib.loads(plist_bytes)

        assert plist["StartInterval"] == 1800
        assert "StartCalendarInterval" not in plist

    def test_plist_with_calendar_schedule(self, tmp_path):
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist("test", workspace, log_path, schedule="*:00")
        plist = plistlib.loads(plist_bytes)

        assert "StartInterval" not in plist
        assert plist["StartCalendarInterval"] == [{"Minute": 0}]

    def test_plist_with_env(self, tmp_path):
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist(
            "test", workspace, log_path, env={"FOO": "bar", "BAZ": "qux"}
        )
        plist = plistlib.loads(plist_bytes)

        assert plist["EnvironmentVariables"] == {"FOO": "bar", "BAZ": "qux"}

    def test_plist_escapes_special_characters(self, tmp_path):
        """Verify plistlib properly escapes XML-special characters."""
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist(
            "test&<agent>", workspace, log_path, env={"KEY": "val<ue>&"}
        )
        # Should produce valid XML that can be parsed back
        plist = plistlib.loads(plist_bytes)
        assert plist["Label"] == "org.gptme.agent.test&<agent>"
        assert plist["EnvironmentVariables"]["KEY"] == "val<ue>&"

    def test_plist_no_schedule(self, tmp_path):
        workspace = tmp_path / "agent"
        log_path = tmp_path / "agent.log"

        plist_bytes = _build_launchd_plist("test", workspace, log_path)
        plist = plistlib.loads(plist_bytes)

        assert "StartInterval" not in plist
        assert "StartCalendarInterval" not in plist


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

    def test_install_creates_service_and_timer(self, manager, tmp_path):
        """Test that install writes service and timer files."""
        workspace = tmp_path / "my-agent"
        workspace.mkdir()

        with patch.object(
            manager, "_run_systemctl", return_value=MagicMock(returncode=0)
        ):
            result = manager.install("test", workspace, schedule="*:00/30")

        assert result is True
        service_path = manager._service_path("test")
        timer_path = manager._timer_path("test")
        assert service_path.exists()
        assert timer_path.exists()

        service_content = service_path.read_text()
        assert str(workspace) in service_content
        assert "autonomous-run.sh" in service_content
        assert "Type=oneshot" in service_content

        timer_content = timer_path.read_text()
        assert "OnCalendar=*:00/30" in timer_content
        assert "Persistent=true" in timer_content

    def test_install_with_env(self, manager, tmp_path):
        """Test that install includes environment variables."""
        workspace = tmp_path / "my-agent"
        workspace.mkdir()

        with patch.object(
            manager, "_run_systemctl", return_value=MagicMock(returncode=0)
        ):
            manager.install("test", workspace, env={"API_KEY": "secret"})

        service_content = manager._service_path("test").read_text()
        assert "Environment=API_KEY=secret" in service_content

    def test_start_enables_and_starts_timer(self, manager):
        """Test that start both enables and starts the timer."""
        calls = []

        def mock_systemctl(*args):
            calls.append(args)
            return MagicMock(returncode=0)

        with patch.object(manager, "_run_systemctl", side_effect=mock_systemctl):
            result = manager.start("test")

        assert result is True
        assert ("enable", "gptme-agent-test.timer") in calls
        assert ("start", "gptme-agent-test.timer") in calls

    def test_stop_disables_and_stops_timer(self, manager):
        """Test that stop both stops and disables the timer."""
        calls = []

        def mock_systemctl(*args):
            calls.append(args)
            return MagicMock(returncode=0)

        with patch.object(manager, "_run_systemctl", side_effect=mock_systemctl):
            result = manager.stop("test")

        assert result is True
        assert ("stop", "gptme-agent-test.timer") in calls
        assert ("disable", "gptme-agent-test.timer") in calls

    def test_run_starts_service_not_timer(self, manager):
        """Test that run starts the service directly (not the timer)."""
        calls = []

        def mock_systemctl(*args):
            calls.append(args)
            return MagicMock(returncode=0)

        with patch.object(manager, "_run_systemctl", side_effect=mock_systemctl):
            result = manager.run("test")

        assert result is True
        assert ("start", "gptme-agent-test.service") in calls

    def test_uninstall_removes_files(self, manager, tmp_path):
        """Test that uninstall removes service and timer files."""
        # Create fake files
        manager._service_path("test").write_text("[Unit]\n")
        manager._timer_path("test").write_text("[Unit]\n")

        with patch.object(
            manager, "_run_systemctl", return_value=MagicMock(returncode=0)
        ):
            result = manager.uninstall("test")

        assert result is True
        assert not manager._service_path("test").exists()
        assert not manager._timer_path("test").exists()


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

    def test_label(self, manager):
        """Test label generation."""
        assert manager._label("test") == "org.gptme.agent.test"

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

    def test_install_creates_valid_plist(self, manager, tmp_path):
        """Test that install creates a valid plist file."""
        workspace = tmp_path / "my-agent"
        workspace.mkdir()

        with patch.object(
            manager, "_run_launchctl", return_value=MagicMock(returncode=0)
        ):
            with patch.object(manager, "_is_loaded", return_value=False):
                result = manager.install("test", workspace, schedule="*:00/30")

        assert result is True
        plist_path = manager._plist_path("test")
        assert plist_path.exists()

        # Verify it's valid XML that plistlib can parse
        plist = plistlib.loads(plist_path.read_bytes())
        assert plist["Label"] == "org.gptme.agent.test"
        assert plist["StartInterval"] == 1800

    def test_install_unloads_existing(self, manager, tmp_path):
        """Test that install unloads existing agent before reinstalling."""
        workspace = tmp_path / "my-agent"
        workspace.mkdir()
        calls = []

        def mock_launchctl(*args):
            calls.append(args)
            return MagicMock(returncode=0)

        with (
            patch.object(manager, "_run_launchctl", side_effect=mock_launchctl),
            patch.object(manager, "_is_loaded", return_value=True),
        ):
            manager.install("test", workspace)

        # Should unload before loading
        unload_calls = [c for c in calls if c[0] == "unload"]
        load_calls = [c for c in calls if c[0] == "load"]
        assert len(unload_calls) == 1
        assert len(load_calls) == 1

    def test_start_when_already_loaded(self, manager, tmp_path):
        """Test that start returns True when already loaded."""
        # Create a plist file
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with patch.object(manager, "_is_loaded", return_value=True):
            result = manager.start("test")

        assert result is True

    def test_start_loads_plist(self, manager, tmp_path):
        """Test that start loads the plist when not loaded."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with (
            patch.object(manager, "_is_loaded", return_value=False),
            patch.object(
                manager, "_run_launchctl", return_value=MagicMock(returncode=0)
            ) as mock_ctl,
        ):
            result = manager.start("test")

        assert result is True
        mock_ctl.assert_called_once_with("load", str(plist_path))

    def test_start_fails_without_plist(self, manager):
        """Test that start fails if plist doesn't exist."""
        result = manager.start("nonexistent")
        assert result is False

    def test_stop_when_already_unloaded(self, manager, tmp_path):
        """Test that stop returns True when already unloaded."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with patch.object(manager, "_is_loaded", return_value=False):
            result = manager.stop("test")

        assert result is True

    def test_stop_unloads_plist(self, manager, tmp_path):
        """Test that stop unloads the plist when loaded."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with (
            patch.object(manager, "_is_loaded", return_value=True),
            patch.object(
                manager, "_run_launchctl", return_value=MagicMock(returncode=0)
            ) as mock_ctl,
        ):
            result = manager.stop("test")

        assert result is True
        mock_ctl.assert_called_once_with("unload", str(plist_path))

    def test_run_ensures_loaded_first(self, manager, tmp_path):
        """Test that run loads the plist before starting."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))
        calls = []

        def mock_launchctl(*args):
            calls.append(args)
            return MagicMock(returncode=0)

        with (
            patch.object(manager, "_is_loaded", return_value=False),
            patch.object(manager, "_run_launchctl", side_effect=mock_launchctl),
        ):
            result = manager.run("test")

        assert result is True
        # Should load first, then start
        assert ("load", str(plist_path)) in calls
        assert ("start", "org.gptme.agent.test") in calls

    def test_run_fails_without_plist(self, manager):
        """Test that run fails if plist doesn't exist."""
        with patch.object(manager, "_is_loaded", return_value=False):
            result = manager.run("nonexistent")

        assert result is False

    def test_uninstall_removes_plist(self, manager, tmp_path):
        """Test that uninstall removes the plist file."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with patch.object(manager, "_is_loaded", return_value=True):
            with patch.object(
                manager, "_run_launchctl", return_value=MagicMock(returncode=0)
            ):
                result = manager.uninstall("test")

        assert result is True
        assert not plist_path.exists()

    def test_status_not_installed(self, manager):
        """Test status for agent with no plist."""
        result = manager.status("nonexistent")
        assert result is None

    def test_status_installed_not_loaded(self, manager, tmp_path):
        """Test status for agent with plist but not loaded."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        with patch.object(manager, "_is_loaded", return_value=False):
            result = manager.status("test")

        assert result is not None
        assert result.name == "test"
        assert result.running is False
        assert result.enabled is False

    def test_status_running(self, manager, tmp_path):
        """Test status for running agent."""
        plist_path = manager._plist_path("test")
        plist_path.write_bytes(plistlib.dumps({"Label": "org.gptme.agent.test"}))

        mock_result = MagicMock(returncode=0, stdout="1234\t0\torg.gptme.agent.test\n")

        with (
            patch.object(manager, "_is_loaded", return_value=True),
            patch.object(manager, "_run_launchctl", return_value=mock_result),
        ):
            result = manager.status("test")

        assert result is not None
        assert result.running is True
        assert result.pid == 1234
        assert result.enabled is True

    def test_logs_missing_file(self, manager):
        """Test logs when log file doesn't exist."""
        result = manager.logs("nonexistent")
        assert "No logs found" in result

    def test_logs_reads_file(self, manager, tmp_path):
        """Test logs reads from log file."""
        log_path = manager._log_path("test")
        log_path.write_text("line1\nline2\nline3\n")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="line1\nline2\nline3\n")
            result = manager.logs("test", lines=50)

        assert result == "line1\nline2\nline3\n"


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
        assert "Create a new agent workspace" in result.output

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
