"""
Service manager abstraction for agent management.

Provides a unified interface over systemd (Linux) and launchd (macOS)
for managing gptme agent services.
"""

import logging
import os
import platform
import plistlib
import re
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

        # Enable and start timer if schedule provided
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
        """Enable and start the agent timer (resume scheduled runs)."""
        self._run_systemctl("enable", f"gptme-agent-{name}.timer")
        result = self._run_systemctl("start", f"gptme-agent-{name}.timer")
        return result.returncode == 0

    def stop(self, name: str) -> bool:
        """Stop and disable the agent timer (pause scheduled runs)."""
        self._run_systemctl("stop", f"gptme-agent-{name}.timer")
        result = self._run_systemctl("disable", f"gptme-agent-{name}.timer")
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


def parse_schedule(schedule: str) -> dict:
    """Parse a systemd OnCalendar schedule string into launchd config.

    Supports common patterns:
    - ``*:00/30``     -> every 30 minutes (StartInterval)
    - ``*:00``        -> every hour on the hour (StartCalendarInterval)
    - ``*-*-* HH:MM`` -> daily at HH:MM
    - ``Mon *:00``    -> every hour on Mondays

    Returns a dict with either ``StartInterval`` (int seconds) or
    ``StartCalendarInterval`` (list of dicts).
    """
    schedule = schedule.strip()

    # Interval pattern: *:00/N or *:N/M  -> every N minutes
    m = re.match(r"^\*:\d+/(\d+)$", schedule)
    if m:
        interval_minutes = int(m.group(1))
        return {"StartInterval": interval_minutes * 60}

    # Hourly: *:00 or *:MM
    m = re.match(r"^\*:(\d+)$", schedule)
    if m:
        minute = int(m.group(1))
        return {"StartCalendarInterval": [{"Minute": minute}]}

    # Daily: *-*-* HH:MM
    m = re.match(r"^\*-\*-\*\s+(\d+):(\d+)$", schedule)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        return {"StartCalendarInterval": [{"Hour": hour, "Minute": minute}]}

    # Day of week: Mon/Tue/Wed/Thu/Fri/Sat/Sun HH:MM or *:MM
    day_map = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 0}
    m = re.match(r"^(\w+)\s+\*:(\d+)$", schedule, re.IGNORECASE)
    if m and m.group(1).lower() in day_map:
        weekday = day_map[m.group(1).lower()]
        minute = int(m.group(2))
        return {"StartCalendarInterval": [{"Weekday": weekday, "Minute": minute}]}

    m = re.match(r"^(\w+)\s+(\d+):(\d+)$", schedule, re.IGNORECASE)
    if m and m.group(1).lower() in day_map:
        weekday = day_map[m.group(1).lower()]
        hour, minute = int(m.group(2)), int(m.group(3))
        return {
            "StartCalendarInterval": [
                {"Weekday": weekday, "Hour": hour, "Minute": minute}
            ]
        }

    logger.warning(f"Unrecognized schedule format '{schedule}', defaulting to hourly")
    return {"StartCalendarInterval": [{"Minute": 0}]}


def _build_launchd_plist(
    name: str,
    workspace: Path,
    log_path: Path,
    schedule: str | None = None,
    env: dict[str, str] | None = None,
) -> bytes:
    """Build a launchd plist as bytes using plistlib.

    Uses plistlib for safe XML generation (proper escaping of all values).
    """
    plist: dict = {
        "Label": f"org.gptme.agent.{name}",
        "ProgramArguments": [
            str(workspace / "scripts" / "runs" / "autonomous" / "autonomous-run.sh"),
        ],
        "WorkingDirectory": str(workspace),
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "RunAtLoad": False,
    }

    if schedule:
        schedule_config = parse_schedule(schedule)
        if "StartInterval" in schedule_config:
            plist["StartInterval"] = schedule_config["StartInterval"]
        elif "StartCalendarInterval" in schedule_config:
            plist["StartCalendarInterval"] = schedule_config["StartCalendarInterval"]

    if env:
        plist["EnvironmentVariables"] = env

    return plistlib.dumps(plist, sort_keys=False)


class LaunchdManager(ServiceManager):
    """Launchd service manager for macOS."""

    def __init__(self):
        self.agents_dir = Path.home() / "Library" / "LaunchAgents"
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = Path.home() / "Library" / "Logs" / "gptme"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _label(self, name: str) -> str:
        return f"org.gptme.agent.{name}"

    def _plist_path(self, name: str) -> Path:
        return self.agents_dir / f"org.gptme.agent.{name}.plist"

    def _log_path(self, name: str) -> Path:
        return self.logs_dir / f"agent-{name}.log"

    def _run_launchctl(self, *args: str) -> subprocess.CompletedProcess:
        """Run launchctl command."""
        cmd = ["launchctl", *args]
        return subprocess.run(cmd, capture_output=True, text=True)

    def _is_loaded(self, name: str) -> bool:
        """Check if the agent plist is currently loaded."""
        result = self._run_launchctl("list", self._label(name))
        return result.returncode == 0

    def _ensure_loaded(self, name: str) -> bool:
        """Ensure the agent plist is loaded. Returns False if plist doesn't exist."""
        if self._is_loaded(name):
            return True
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return False
        result = self._run_launchctl("load", str(plist_path))
        return result.returncode == 0

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

        # Unload existing if present (so we can overwrite)
        if self._is_loaded(name):
            self._run_launchctl("unload", str(self._plist_path(name)))

        # Build plist using plistlib for safe XML generation
        plist_bytes = _build_launchd_plist(
            name=name,
            workspace=workspace,
            log_path=log_path,
            schedule=schedule,
            env=env,
        )

        # Write plist file
        plist_path = self._plist_path(name)
        plist_path.write_bytes(plist_bytes)
        logger.info(f"Created plist file: {plist_path}")

        # Load the agent
        result = self._run_launchctl("load", str(plist_path))
        if result.returncode != 0:
            logger.error(f"Failed to load plist: {result.stderr}")
            return False

        return True

    def uninstall(self, name: str) -> bool:
        """Remove launchd plist file."""
        plist_path = self._plist_path(name)

        # Unload first
        if self._is_loaded(name):
            self._run_launchctl("unload", str(plist_path))

        # Remove file
        if plist_path.exists():
            plist_path.unlink()

        return True

    def start(self, name: str) -> bool:
        """Start the agent (load plist to enable scheduled runs)."""
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return False
        if self._is_loaded(name):
            # Already loaded/started
            return True
        result = self._run_launchctl("load", str(plist_path))
        return result.returncode == 0

    def stop(self, name: str) -> bool:
        """Stop the agent (unload plist to disable scheduled runs)."""
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return False
        if not self._is_loaded(name):
            # Already stopped
            return True
        result = self._run_launchctl("unload", str(plist_path))
        return result.returncode == 0

    def restart(self, name: str) -> bool:
        """Restart the agent (reload plist to pick up changes)."""
        self.stop(name)
        return self.start(name)

    def run(self, name: str) -> bool:
        """Trigger an immediate one-time run of the agent.

        Ensures the plist is loaded first, then kicks off an immediate run.
        """
        if not self._ensure_loaded(name):
            return False
        result = self._run_launchctl("start", self._label(name))
        return result.returncode == 0

    def status(self, name: str) -> ServiceStatus | None:
        """Get status of the agent."""
        plist_path = self._plist_path(name)
        if not plist_path.exists():
            return None

        result = self._run_launchctl("list", self._label(name))
        if result.returncode != 0:
            return ServiceStatus(name=name, running=False, enabled=False)
        pid = None
        exit_code = None

        for line in result.stdout.strip().split("\n"):
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
