"""
Service manager abstraction for agent management.

Provides a unified interface over systemd (Linux) and launchd (macOS)
for managing gptme agent services.
"""

import logging
import os
import platform
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ServiceManagerType = Literal["systemd", "launchd", "none"]


@dataclass
class ServiceStatus:
    """Status of an agent service."""

    name: str
    running: bool
    enabled: bool
    pid: int | None = None
    uptime: str | None = None
    last_run: str | None = None
    next_run: str | None = None
    exit_code: int | None = None


def detect_service_manager() -> ServiceManagerType:
    """Detect the available service manager on the current system."""
    system = platform.system()

    if system == "Linux":
        # Check if systemd is available
        if Path("/run/systemd/system").exists():
            return "systemd"
    elif system == "Darwin":
        # macOS uses launchd
        return "launchd"

    return "none"


class ServiceManager(ABC):
    """Abstract base class for service managers."""

    @abstractmethod
    def install(
        self,
        name: str,
        workspace: Path,
        schedule: str | None = None,
        env: dict[str, str] | None = None,
    ) -> bool:
        """Install agent service files."""
        ...

    @abstractmethod
    def uninstall(self, name: str) -> bool:
        """Remove agent service files."""
        ...

    @abstractmethod
    def start(self, name: str) -> bool:
        """Start the agent service."""
        ...

    @abstractmethod
    def stop(self, name: str) -> bool:
        """Stop the agent service."""
        ...

    @abstractmethod
    def restart(self, name: str) -> bool:
        """Restart the agent service."""
        ...

    @abstractmethod
    def run(self, name: str) -> bool:
        """Trigger an immediate one-time run of the agent."""
        ...

    @abstractmethod
    def status(self, name: str) -> ServiceStatus | None:
        """Get status of the agent service."""
        ...

    @abstractmethod
    def logs(self, name: str, lines: int = 50, follow: bool = False) -> str:
        """Get logs from the agent service."""
        ...

    @abstractmethod
    def list_agents(self) -> list[str]:
        """List all installed gptme agents."""
        ...


class SystemdManager(ServiceManager):
    """Systemd service manager for Linux."""

    def __init__(self):
        self.user_dir = Path.home() / ".config" / "systemd" / "user"
        self.user_dir.mkdir(parents=True, exist_ok=True)

    def _service_path(self, name: str) -> Path:
        return self.user_dir / f"gptme-agent-{name}.service"

    def _timer_path(self, name: str) -> Path:
        return self.user_dir / f"gptme-agent-{name}.timer"

    def _run_systemctl(self, *args: str) -> subprocess.CompletedProcess:
        """Run systemctl with user flag."""
        cmd = ["systemctl", "--user", *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    def install(
        self,
        name: str,
        workspace: Path,
        schedule: str | None = None,
        env: dict[str, str] | None = None,
    ) -> bool:
        """Install systemd service and timer files."""
        workspace = workspace.resolve()

        # Environment variables
        env_lines = ""
        if env:
            env_lines = "\n".join(f"Environment={k}={v}" for k, v in env.items())

        # Service file content
        service_content = f"""[Unit]
Description=gptme agent: {name}
After=network.target

[Service]
Type=oneshot
WorkingDirectory={workspace}
ExecStart={workspace}/scripts/runs/autonomous/autonomous-run.sh
{env_lines}
TimeoutStartSec=1800

[Install]
WantedBy=default.target
"""

        # Write service file
        service_path = self._service_path(name)
        service_path.write_text(service_content)
        logger.info(f"Created service file: {service_path}")

        # Timer file (if schedule provided)
        if schedule:
            timer_content = f"""[Unit]
Description=Timer for gptme agent: {name}
Requires=gptme-agent-{name}.service

[Timer]
OnCalendar={schedule}
Persistent=true

[Install]
WantedBy=timers.target
"""
            timer_path = self._timer_path(name)
            timer_path.write_text(timer_content)
            logger.info(f"Created timer file: {timer_path}")

        # Reload systemd
        self._run_systemctl("daemon-reload")

        # Enable timer if schedule provided
        if schedule:
            self._run_systemctl("enable", f"gptme-agent-{name}.timer")
            self._run_systemctl("start", f"gptme-agent-{name}.timer")

        return True

    def uninstall(self, name: str) -> bool:
        """Remove systemd service and timer files."""
        # Stop and disable
        self._run_systemctl("stop", f"gptme-agent-{name}.timer")
        self._run_systemctl("disable", f"gptme-agent-{name}.timer")
        self._run_systemctl("stop", f"gptme-agent-{name}.service")
        self._run_systemctl("disable", f"gptme-agent-{name}.service")

        # Remove files
        service_path = self._service_path(name)
        timer_path = self._timer_path(name)

        if service_path.exists():
            service_path.unlink()
        if timer_path.exists():
            timer_path.unlink()

        self._run_systemctl("daemon-reload")
        return True

    def start(self, name: str) -> bool:
        """Start the agent timer (enable scheduled runs)."""
        result = self._run_systemctl("start", f"gptme-agent-{name}.timer")
        return result.returncode == 0

    def stop(self, name: str) -> bool:
        """Stop the agent timer (prevent scheduled runs)."""
        result = self._run_systemctl("stop", f"gptme-agent-{name}.timer")
        return result.returncode == 0

    def restart(self, name: str) -> bool:
        """Restart the agent timer."""
        self.stop(name)
        return self.start(name)

    def run(self, name: str) -> bool:
        """Trigger an immediate one-time run of the agent service."""
        result = self._run_systemctl("start", f"gptme-agent-{name}.service")
        return result.returncode == 0

    def status(self, name: str) -> ServiceStatus | None:
        """Get status of the agent service."""
        # Check service
        result = self._run_systemctl(
            "show",
            f"gptme-agent-{name}.service",
            "--property=ActiveState,SubState,MainPID,ExecMainExitTimestamp,ExecMainStatus",
        )
        if result.returncode != 0:
            return None

        props = dict(
            line.split("=", 1)
            for line in result.stdout.strip().split("\n")
            if "=" in line
        )

        # Check timer
        timer_result = self._run_systemctl(
            "show",
            f"gptme-agent-{name}.timer",
            "--property=ActiveState,NextElapseUSecRealtime,LastTriggerUSecRealtime",
        )
        timer_props = {}
        if timer_result.returncode == 0:
            timer_props = dict(
                line.split("=", 1)
                for line in timer_result.stdout.strip().split("\n")
                if "=" in line
            )

        running = props.get("ActiveState") == "active"
        enabled = (
            self._run_systemctl("is-enabled", f"gptme-agent-{name}.timer").returncode
            == 0
        )

        return ServiceStatus(
            name=name,
            running=running,
            enabled=enabled,
            pid=int(props.get("MainPID", 0)) or None,
            last_run=timer_props.get("LastTriggerUSecRealtime"),
            next_run=timer_props.get("NextElapseUSecRealtime"),
            exit_code=int(props.get("ExecMainStatus", 0)) or None,
        )

    def logs(self, name: str, lines: int = 50, follow: bool = False) -> str:
        """Get logs from journalctl."""
        cmd = [
            "journalctl",
            "--user",
            "-u",
            f"gptme-agent-{name}.service",
            "-n",
            str(lines),
            "--no-pager",
            "-o",
            "cat",
        ]
        if follow:
            cmd.append("-f")
            # For follow mode, we need to exec
            os.execvp("journalctl", cmd)

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout

    def list_agents(self) -> list[str]:
        """List all installed gptme agents."""
        agents = []
        for path in self.user_dir.glob("gptme-agent-*.service"):
            name = path.stem.replace("gptme-agent-", "")
            agents.append(name)
        return agents


class LaunchdManager(ServiceManager):
    """Launchd service manager for macOS."""

    def __init__(self):
        self.agents_dir = Path.home() / "Library" / "LaunchAgents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = Path.home() / "Library" / "Logs" / "gptme"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _plist_path(self, name: str) -> Path:
        return self.agents_dir / f"org.gptme.agent.{name}.plist"

    def _log_path(self, name: str) -> Path:
        return self.logs_dir / f"agent-{name}.log"

    def _run_launchctl(self, *args: str) -> subprocess.CompletedProcess:
        """Run launchctl command."""
        cmd = ["launchctl", *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _cron_to_launchd(self, schedule: str) -> dict:
        """Convert cron-like schedule to launchd StartCalendarInterval."""
        # Simple parsing for common patterns
        # Format: minute hour day-of-month month day-of-week
        # or systemd-like: *:00/30 (every 30 minutes)

        if "/" in schedule and ":" in schedule:
            # Systemd-like interval: *:00/30 means every 30 minutes
            parts = schedule.split(":")
            if len(parts) == 2:
                minute_part = parts[1]
                if "/" in minute_part:
                    interval = int(minute_part.split("/")[1])
                    return {"StartInterval": interval * 60}

        # Default: every 2 hours
        return {"StartCalendarInterval": {"Hour": [6, 8, 10, 12, 14, 16, 18, 20]}}

    def install(
        self,
        name: str,
        workspace: Path,
        schedule: str | None = None,
        env: dict[str, str] | None = None,
    ) -> bool:
        """Install launchd plist file."""
        workspace = workspace.resolve()
        log_path = self._log_path(name)

        # Build plist content
        schedule_config = self._cron_to_launchd(schedule or "")

        # Environment dict for plist
        env_dict = env or {}

        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>org.gptme.agent.{name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{workspace}/scripts/runs/autonomous/autonomous-run.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workspace}</string>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
    <key>RunAtLoad</key>
    <false/>
"""

        # Add schedule if provided
        if "StartInterval" in schedule_config:
            plist_content += f"""    <key>StartInterval</key>
    <integer>{schedule_config["StartInterval"]}</integer>
"""
        elif "StartCalendarInterval" in schedule_config:
            plist_content += """    <key>StartCalendarInterval</key>
    <array>
"""
            for hour in schedule_config["StartCalendarInterval"]["Hour"]:
                plist_content += f"""        <dict>
            <key>Hour</key>
            <integer>{hour}</integer>
            <key>Minute</key>
            <integer>0</integer>
        </dict>
"""
            plist_content += """    </array>
"""

        # Add environment variables
        if env_dict:
            plist_content += """    <key>EnvironmentVariables</key>
    <dict>
"""
            for key, value in env_dict.items():
                plist_content += f"""        <key>{key}</key>
        <string>{value}</string>
"""
            plist_content += """    </dict>
"""

        plist_content += """</dict>
</plist>
"""

        # Write plist file
        plist_path = self._plist_path(name)
        plist_path.write_text(plist_content)
        logger.info(f"Created plist file: {plist_path}")

        # Load the agent
        self._run_launchctl("load", str(plist_path))

        return True

    def uninstall(self, name: str) -> bool:
        """Remove launchd plist file."""
        plist_path = self._plist_path(name)

        # Unload first
        self._run_launchctl("unload", str(plist_path))

        # Remove file
        if plist_path.exists():
            plist_path.unlink()

        return True

    def start(self, name: str) -> bool:
        """Start the agent (enable scheduled runs by loading plist)."""
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return False
        result = self._run_launchctl("load", str(plist_path))
        return result.returncode == 0

    def stop(self, name: str) -> bool:
        """Stop the agent (disable scheduled runs by unloading plist)."""
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return False
        result = self._run_launchctl("unload", str(plist_path))
        return result.returncode == 0

    def restart(self, name: str) -> bool:
        """Restart the agent (reload plist to pick up changes)."""
        self.stop(name)
        return self.start(name)

    def run(self, name: str) -> bool:
        """Trigger an immediate one-time run of the agent."""
        result = self._run_launchctl("start", f"org.gptme.agent.{name}")
        return result.returncode == 0

    def status(self, name: str) -> ServiceStatus | None:
        """Get status of the agent."""
        result = self._run_launchctl("list", f"org.gptme.agent.{name}")
        if result.returncode != 0:
            # Agent not loaded
            plist_path = self._plist_path(name)
            if plist_path.exists():
                return ServiceStatus(name=name, running=False, enabled=False)
            return None

        # Parse output
        lines = result.stdout.strip().split("\n")
        pid = None
        exit_code = None

        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                # Format: PID Status Label
                try:
                    pid = int(parts[0]) if parts[0] != "-" else None
                    exit_code = int(parts[1]) if parts[1] != "-" else None
                except ValueError:
                    pass

        return ServiceStatus(
            name=name,
            running=pid is not None and pid > 0,
            enabled=True,
            pid=pid,
            exit_code=exit_code,
        )

    def logs(self, name: str, lines: int = 50, follow: bool = False) -> str:
        """Get logs from log file."""
        log_path = self._log_path(name)

        if not log_path.exists():
            return f"No logs found at {log_path}"

        if follow:
            # For follow mode, exec tail
            os.execvp("tail", ["tail", "-f", str(log_path)])

        # Get last N lines
        result = subprocess.run(
            ["tail", "-n", str(lines), str(log_path)],
            capture_output=True,
            text=True,
        )
        return result.stdout

    def list_agents(self) -> list[str]:
        """List all installed gptme agents."""
        agents = []
        for path in self.agents_dir.glob("org.gptme.agent.*.plist"):
            name = path.stem.replace("org.gptme.agent.", "")
            agents.append(name)
        return agents


def get_service_manager() -> ServiceManager | None:
    """Get the appropriate service manager for the current system."""
    manager_type = detect_service_manager()

    if manager_type == "systemd":
        return SystemdManager()
    elif manager_type == "launchd":
        return LaunchdManager()
    else:
        return None
