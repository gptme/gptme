"""Parallel agent awareness via process scanning.

Detects other agent instances (gptme, claude, codex, aider, goose, opencode,
amp, etc.) running in the same workspace by scanning processes and their CWDs. Warns the user on
session start if parallel agents are found.

No lock files — process-based detection is cleaner, richer, and self-healing:
  - No stale locks on crash/SIGKILL
  - Detects all agent runtimes, not just gptme
  - Extracts rich metadata (model, mode, branch, conversation ID)
  - Works cross-platform (Linux /proc, macOS ps+lsof)

See: https://github.com/gptme/gptme/issues/1505
See: https://github.com/gptme/gptme/issues/554
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..logmanager import LogManager

logger = logging.getLogger(__name__)


# --- Supported agent runtimes ---
# Binary name → runtime key.  Add new runtimes here.
AGENT_BINARIES: dict[str, str] = {
    "claude": "claude-code",
    "codex": "codex",
    "gptme": "gptme",
    "aider": "aider",
    "goose": "goose",  # Block's open-source AI agent (Rust CLI)
    "goosed": "goose",  # goose backend/server process
    "opencode": "opencode",  # OpenCode terminal AI agent (Go CLI)
    "amp": "amp",  # Sourcegraph Amp coding agent (Node.js CLI)
    # Not yet: "cline", "continue", "cursor" (VS Code extensions / desktop apps, harder to detect)
}

# Fallback patterns for interpreter-wrapped invocations (e.g. ``python3 /path/to/gptme``).
# Matched against the first 3 cmdline args only to avoid false positives.
AGENT_CMDLINE_PATTERNS: list[tuple[str, str]] = [
    (r"/bin/gptme\b", "gptme"),
    (r"/bin/claude\b", "claude-code"),
    (r"/bin/codex\b", "codex"),
    (r"/bin/aider\b", "aider"),
    (r"/bin/goose\b", "goose"),
    (r"/bin/goosed\b", "goose"),
    (r"/bin/opencode\b", "opencode"),
    (r"/bin/amp\b", "amp"),
]

# Exclude these even if they match a runtime binary (background services, wrappers).
EXCLUDE_CMDLINE_PATTERNS: list[str] = [
    r"\.claude/shell-snapshots/",  # CC's bash subprocesses
    r"server\.py\b",  # gptme-webui server.py
    r"twitter-loop",  # twitter automation loop
    r"discord_bot",  # discord bot
    r"^(/usr)?/bin/sh -c ",  # shell wrappers (child is the real agent)
    r"^(/usr)?/bin/timeout \d",  # timeout wrappers
    r"^(/usr)?/bin/tee\b",  # tee pipes alongside agents
]

# Staleness thresholds (seconds).
# Processes exceeding these with low CPU activity are flagged as potentially stale.
STALE_THRESHOLDS: dict[str | None, int | None] = {
    "autonomous": 7200,  # 2h
    "interactive": 86400,  # 24h
    "server": None,  # servers are expected to be long-running
    "unknown": 3600,  # 1h
}

# Minimum CPU ratio (cpu_time / uptime) to consider a process "active".
MIN_CPU_RATIO = 0.001  # 0.1%


# ---------------------------------------------------------------------------
#  Data model
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Metadata about a detected agent process."""

    pid: int
    runtime: str  # e.g. "claude-code", "gptme", "codex"
    cwd: str
    model: str | None = None
    mode: str | None = None  # "interactive", "autonomous", "server", "unknown"
    branch: str | None = None
    conversation_id: str | None = None
    log_dir: str | None = None
    cmdline_summary: str = ""
    uptime_seconds: int | None = None
    cpu_seconds: float | None = None
    process_state: str | None = None  # S=sleeping, R=running, T=stopped, Z=zombie
    stale: bool = False
    stale_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
#  Cross-platform process introspection
# ---------------------------------------------------------------------------


def _get_process_cwd(pid: int) -> str | None:
    """Get process CWD, cross-platform."""
    system = platform.system()
    if system == "Linux":
        try:
            return os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
    elif system == "Darwin":
        try:
            out = subprocess.check_output(
                ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in out.splitlines():
                if line.startswith("n"):
                    return line[1:]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
    # TODO: Windows — use wmic or ctypes
    return None


def _get_process_cmdline(pid: int) -> list[str]:
    """Get process command-line args, cross-platform."""
    system = platform.system()
    if system == "Linux":
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return [a for a in raw.decode("utf-8", errors="replace").split("\0") if a]
        except OSError:
            return []
    elif system == "Darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "args="],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            return shlex.split(out) if out else []
        except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
            return []
    # TODO: Windows — use wmic
    return []


def _get_all_pids() -> list[int]:
    """Get all accessible PIDs, cross-platform."""
    system = platform.system()
    if system == "Linux":
        return [int(e) for e in os.listdir("/proc") if e.isdigit()]
    if system == "Darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-eo", "pid="], stderr=subprocess.DEVNULL, text=True
            )
            return [int(line.strip()) for line in out.splitlines() if line.strip()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []
    # TODO: Windows — use tasklist or ctypes
    return []


def _get_process_timing(pid: int) -> tuple[int | None, float | None, str | None]:
    """Get process uptime, CPU time, and state.

    Returns:
        (uptime_seconds, cpu_seconds, state_char)
    """
    import time

    system = platform.system()
    if system == "Linux":
        try:
            stat_raw = Path(f"/proc/{pid}/stat").read_text()
            # Parse /proc/pid/stat — comm (field 2) can contain spaces/parens
            right = stat_raw[stat_raw.rfind(")") + 2 :]
            fields = right.split()
            state = fields[0]
            utime = int(fields[11])
            stime = int(fields[12])
            starttime = int(fields[19])

            clk_tck = os.sysconf("SC_CLK_TCK")

            boot_time = None
            for line in Path("/proc/stat").read_text().splitlines():
                if line.startswith("btime "):
                    boot_time = int(line.split()[1])
                    break
            if boot_time is None:
                return None, None, state

            start_sec = boot_time + starttime // clk_tck
            uptime_s = int(time.time()) - start_sec
            cpu_s = (utime + stime) / clk_tck
            return max(0, uptime_s), cpu_s, state
        except (OSError, IndexError, ValueError):
            return None, None, None
    elif system == "Darwin":
        try:
            out = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "etime=,cputime=,stat="],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            if not out:
                return None, None, None
            parts = out.split()
            mac_uptime: int | None = _parse_etime(parts[0]) if parts else None
            mac_cpu: int | None = _parse_etime(parts[1]) if len(parts) > 1 else None
            mac_state: str | None = parts[2][0] if len(parts) > 2 and parts[2] else None
            return (
                mac_uptime,
                float(mac_cpu) if mac_cpu is not None else None,
                mac_state,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None, None, None
    return None, None, None


def _parse_etime(s: str) -> int | None:
    """Parse ps etime/cputime format: ``[[DD-]HH:]MM:SS[.xx]`` → seconds."""
    if not s:
        return None
    try:
        days = 0
        if "-" in s:
            days_str, s = s.split("-", 1)
            days = int(days_str)
        parts = s.split(":")
        if len(parts) == 3:
            h, m, sec = int(parts[0]), int(parts[1]), int(float(parts[2]))
        elif len(parts) == 2:
            h, m, sec = 0, int(parts[0]), int(float(parts[1]))
        else:
            return int(float(parts[0]))
        return days * 86400 + h * 3600 + m * 60 + sec
    except ValueError:
        return None


def _format_duration(seconds: int) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h}h{m:02d}m"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d}d{h}h"


# ---------------------------------------------------------------------------
#  Runtime detection & metadata extraction
# ---------------------------------------------------------------------------


def _get_git_branch(cwd: str) -> str | None:
    """Get the current git branch for a directory."""
    try:
        return (
            subprocess.check_output(
                ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            or None
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def detect_runtime(cmdline: list[str]) -> str | None:
    """Identify which agent runtime a cmdline belongs to."""
    full = " ".join(cmdline)

    # Check exclusion patterns first
    for pattern in EXCLUDE_CMDLINE_PATTERNS:
        if re.search(pattern, full):
            return None

    # Skip shell wrappers
    if len(cmdline) >= 2 and os.path.basename(cmdline[0]) in ("bash", "sh", "zsh"):
        if cmdline[1] == "-c":
            return None

    # Check binary basenames — first arg or path-like args
    for i, arg in enumerate(cmdline):
        if i > 0 and "/" not in arg:
            continue
        basename = re.sub(r"\.exe$", "", os.path.basename(arg))
        if basename in AGENT_BINARIES:
            return AGENT_BINARIES[basename]

    # Fallback: pattern match on first 3 args
    prefix = " ".join(cmdline[:3])
    for pattern, runtime in AGENT_CMDLINE_PATTERNS:
        if re.search(pattern, prefix):
            return runtime

    return None


def _extract_flag(cmdline: list[str], *flags: str) -> str | None:
    """Extract the value of a flag from cmdline (e.g. ``--model opus`` → ``opus``)."""
    for i, arg in enumerate(cmdline):
        for flag in flags:
            if arg == flag and i + 1 < len(cmdline):
                return cmdline[i + 1]
            if arg.startswith(f"{flag}="):
                return arg.split("=", 1)[1]
    return None


def _has_flag(cmdline: list[str], *flags: str) -> bool:
    """Check if any of the given flags are present."""
    return any(arg in flags for arg in cmdline)


# ---------------------------------------------------------------------------
#  Per-runtime metadata parsers
# ---------------------------------------------------------------------------


def _parse_claude_code(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from a Claude Code process."""
    model = _extract_flag(cmdline, "--model", "-m")
    is_pipe = _has_flag(cmdline, "-p", "--print")
    mode = "autonomous" if is_pipe else "interactive"

    conversation_id = None
    log_dir = None
    project_hash = cwd.replace("/", "-")
    project_dir = Path.home() / ".claude" / "projects" / project_hash
    if project_dir.is_dir():
        log_dir = str(project_dir)
        jsonl_files = sorted(
            project_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if jsonl_files:
            conversation_id = jsonl_files[0].stem

    prompt_summary = ""
    if is_pipe:
        non_flag_args = []
        skip_next = False
        for i, arg in enumerate(cmdline):
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-"):
                if arg in (
                    "--model",
                    "-m",
                    "--output-format",
                    "--append-system-prompt-file",
                ):
                    skip_next = True
                continue
            if i > 0:
                non_flag_args.append(arg)
        if non_flag_args:
            prompt_summary = non_flag_args[-1][:120]

    return AgentInfo(
        pid=pid,
        runtime="claude-code",
        cwd=cwd,
        model=model,
        mode=mode,
        conversation_id=conversation_id,
        log_dir=log_dir,
        cmdline_summary=prompt_summary or " ".join(cmdline[:5]),
    )


def _parse_gptme(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from a gptme process."""
    model = _extract_flag(cmdline, "--model", "-m")
    is_non_interactive = _has_flag(cmdline, "--non-interactive", "-n")
    mode = "autonomous" if is_non_interactive else "interactive"

    conversation_name = _extract_flag(cmdline, "--name")
    log_dir = None
    conversation_id = conversation_name

    if conversation_name:
        candidate = Path.home() / ".cache" / "gptme" / "logs" / conversation_name
        if candidate.is_dir():
            log_dir = str(candidate)

    if not log_dir:
        logs_base = Path.home() / ".cache" / "gptme" / "logs"
        if logs_base.is_dir():
            recent = sorted(
                logs_base.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True
            )
            for d in recent[:3]:
                if d.is_dir():
                    log_dir = str(d)
                    conversation_id = d.name
                    break

    if _has_flag(cmdline, "serve"):
        return AgentInfo(
            pid=pid,
            runtime="gptme",
            cwd=cwd,
            model=model,
            mode="server",
            cmdline_summary="gptme-server",
            extra={"is_server": True},
        )

    prompt_summary = ""
    if is_non_interactive:
        for arg in cmdline:
            if arg.endswith(".txt") and os.path.isfile(arg):
                try:
                    with open(arg) as f:
                        prompt_summary = f.readline().strip()[:120]
                except OSError:
                    pass
                break

    return AgentInfo(
        pid=pid,
        runtime="gptme",
        cwd=cwd,
        model=model,
        mode=mode,
        conversation_id=conversation_id,
        log_dir=log_dir,
        cmdline_summary=prompt_summary or " ".join(cmdline[:6]),
    )


def _parse_codex(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from a Codex process."""
    model = _extract_flag(cmdline, "--model", "-m")
    return AgentInfo(
        pid=pid,
        runtime="codex",
        cwd=cwd,
        model=model,
        mode="unknown",
        cmdline_summary=" ".join(cmdline[:5]),
    )


def _parse_aider(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from an aider process."""
    model = _extract_flag(cmdline, "--model")
    return AgentInfo(
        pid=pid,
        runtime="aider",
        cwd=cwd,
        model=model,
        mode="unknown",
        cmdline_summary=" ".join(cmdline[:5]),
    )


def _parse_goose(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from a Goose (Block) process."""
    model = _extract_flag(cmdline, "--model", "-m")
    provider = _extract_flag(cmdline, "--provider", "-p")

    # goosed is the backend server, goose CLI is the agent
    is_server = any(os.path.basename(arg) == "goosed" for arg in cmdline[:2])
    mode = "server" if is_server else "unknown"

    return AgentInfo(
        pid=pid,
        runtime="goose",
        cwd=cwd,
        model=model,
        mode=mode,
        cmdline_summary=" ".join(cmdline[:5]),
        extra={"provider": provider} if provider else {},
    )


def _parse_opencode(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from an OpenCode process."""
    model = _extract_flag(cmdline, "--model", "-m")
    return AgentInfo(
        pid=pid,
        runtime="opencode",
        cwd=cwd,
        model=model,
        mode="unknown",
        cmdline_summary=" ".join(cmdline[:5]),
    )


def _parse_amp(pid: int, cmdline: list[str], cwd: str) -> AgentInfo:
    """Extract metadata from a Sourcegraph Amp process."""
    model = _extract_flag(cmdline, "--model", "-m")
    return AgentInfo(
        pid=pid,
        runtime="amp",
        cwd=cwd,
        model=model,
        mode="unknown",
        cmdline_summary=" ".join(cmdline[:5]),
    )


_RUNTIME_PARSERS = {
    "claude-code": _parse_claude_code,
    "gptme": _parse_gptme,
    "codex": _parse_codex,
    "aider": _parse_aider,
    "goose": _parse_goose,
    "opencode": _parse_opencode,
    "amp": _parse_amp,
}


# ---------------------------------------------------------------------------
#  Staleness heuristics
# ---------------------------------------------------------------------------


def assess_staleness(agent: AgentInfo) -> None:
    """Determine if an agent process is likely stale/stuck."""
    if agent.uptime_seconds is None:
        return

    if agent.process_state in ("Z", "T"):
        agent.stale = True
        state_name = "zombie" if agent.process_state == "Z" else "stopped"
        agent.stale_reason = f"process is {state_name}"
        return

    threshold = STALE_THRESHOLDS.get(agent.mode, STALE_THRESHOLDS["unknown"])
    if threshold is None:
        return  # servers — no staleness check

    if agent.uptime_seconds < threshold:
        return

    if agent.cpu_seconds is not None and agent.uptime_seconds > 0:
        cpu_ratio = agent.cpu_seconds / agent.uptime_seconds
        if cpu_ratio < MIN_CPU_RATIO:
            agent.stale = True
            agent.stale_reason = (
                f"running {_format_duration(agent.uptime_seconds)} "
                f"with {agent.cpu_seconds:.0f}s CPU "
                f"({cpu_ratio:.4%} utilization)"
            )
            return

    if agent.uptime_seconds > threshold * 3:
        agent.stale = True
        agent.stale_reason = (
            f"running {_format_duration(agent.uptime_seconds)} "
            f"(3x beyond {_format_duration(threshold)} threshold)"
        )


# ---------------------------------------------------------------------------
#  Core scan function
# ---------------------------------------------------------------------------


def scan_agents(workspace: str | None = None) -> list[AgentInfo]:
    """Scan all processes for known agent runtimes in the given workspace.

    Args:
        workspace: Only return agents whose CWD is under this path.
                   If None, return all detected agents.
    """
    agents: list[AgentInfo] = []
    my_pid = os.getpid()

    for pid in _get_all_pids():
        if pid == my_pid:
            continue

        cmdline = _get_process_cmdline(pid)
        if not cmdline:
            continue

        runtime = detect_runtime(cmdline)
        if not runtime:
            continue

        cwd = _get_process_cwd(pid)
        if not cwd:
            continue

        # Filter by workspace
        if workspace:
            ws_norm = os.path.realpath(workspace).rstrip("/") + "/"
            cwd_norm = os.path.realpath(cwd).rstrip("/") + "/"
            if not cwd_norm.startswith(ws_norm) and cwd_norm.rstrip(
                "/"
            ) != ws_norm.rstrip("/"):
                continue

        parser = _RUNTIME_PARSERS.get(runtime)
        info = (
            parser(pid, cmdline, cwd)
            if parser
            else AgentInfo(
                pid=pid,
                runtime=runtime,
                cwd=cwd,
                cmdline_summary=" ".join(cmdline[:5]),
            )
        )

        info.branch = _get_git_branch(cwd)

        timing_uptime, timing_cpu, timing_state = _get_process_timing(pid)
        info.uptime_seconds = timing_uptime
        info.cpu_seconds = timing_cpu
        info.process_state = timing_state

        assess_staleness(info)
        agents.append(info)

    # Deduplicate: for same runtime+cwd, keep distinct modes; within same mode keep highest PID
    by_runtime_cwd: dict[tuple[str, str], list[AgentInfo]] = {}
    for a in agents:
        by_runtime_cwd.setdefault((a.runtime, a.cwd), []).append(a)

    deduped: list[AgentInfo] = []
    for group in by_runtime_cwd.values():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            by_mode: dict[str | None, list[AgentInfo]] = {}
            for a in group:
                by_mode.setdefault(a.mode, []).append(a)
            deduped.extend(
                max(mode_group, key=lambda a: a.pid) for mode_group in by_mode.values()
            )

    return sorted(deduped, key=lambda a: a.pid)


def _format_agent_line(agent: AgentInfo) -> str:
    """Format a single agent for the warning message."""
    parts = [f"PID {agent.pid}: {agent.runtime}"]

    details = []
    if agent.model:
        details.append(f"model={agent.model}")
    if agent.mode and agent.mode != "unknown":
        details.append(agent.mode)
    if agent.branch:
        details.append(f"branch={agent.branch}")
    if agent.conversation_id:
        details.append(f"conv={agent.conversation_id}")
    if details:
        parts.append(f"({', '.join(details)})")

    if agent.stale:
        parts.append("[STALE]")
    elif agent.uptime_seconds is not None:
        parts.append(f"up {_format_duration(agent.uptime_seconds)}")

    return " ".join(parts)


# ---------------------------------------------------------------------------
#  Hook implementation
# ---------------------------------------------------------------------------


def session_start_agents(
    logdir: Path,
    workspace: Path | None,
    initial_msgs: list[Message],
) -> Generator[Message | StopPropagation, None, None]:
    """Detect other agent processes in the workspace on session start.

    Scans for known agent runtimes (gptme, claude, codex, aider, etc.)
    whose CWD matches this workspace. If found, warns the user with
    rich metadata about each detected agent.
    """
    if workspace is None:
        return
        yield  # make generator

    try:
        agents = scan_agents(workspace=str(workspace))
    except Exception as e:
        logger.debug(f"Agent scan failed: {e}")
        _init_tracking(str(workspace), [])
        return
        yield

    # Seed the periodic tracker with initial scan results
    _init_tracking(str(workspace), agents)

    if not agents:
        return
        yield

    active = [a for a in agents if not a.stale]
    stale = [a for a in agents if a.stale]

    lines = []
    if active:
        lines.append(f"⚠️  {len(active)} other agent(s) detected in this workspace:")
        lines.extend(f"   {_format_agent_line(a)}" for a in active)
        lines.append("   Consider using a git worktree to avoid conflicts:")
        lines.append("   git worktree add /tmp/worktrees/my-feature")
    if stale:
        lines.append(f"   ({len(stale)} stale process(es) also found, likely harmless)")

    if lines:
        warning = "\n".join(lines)
        yield Message(
            "system",
            f"<workspace-agents-warning>\n{warning}\n</workspace-agents-warning>",
        )
        for a in active:
            logger.warning(
                f"Parallel agent detected: {a.runtime} PID {a.pid} "
                f"({a.mode}, model={a.model})"
            )


# ---------------------------------------------------------------------------
#  Periodic monitoring (detect arrivals/departures during a session)
# ---------------------------------------------------------------------------

# Module-level state for tracking known agents between scans
_known_agents: dict[int, AgentInfo] = {}  # pid → AgentInfo
_workspace_path: str | None = None
_last_scan_time: float = 0.0
_SCAN_INTERVAL = 60.0  # seconds between periodic scans


def _init_tracking(workspace: str, agents: list[AgentInfo]) -> None:
    """Initialize tracking state from the initial session_start scan."""
    global _known_agents, _workspace_path, _last_scan_time
    import time

    _workspace_path = workspace
    _known_agents = {a.pid: a for a in agents}
    _last_scan_time = time.time()


def step_pre_agents(
    manager: LogManager,
) -> Generator[Message | StopPropagation, None, None]:
    """Periodic scan for new/departed agents during the session.

    Runs on each LLM step but throttled to scan at most once per
    ``_SCAN_INTERVAL`` seconds.  Yields messages when agents arrive
    or depart from the workspace.
    """
    import time

    global _known_agents, _last_scan_time

    if _workspace_path is None:
        return
        yield

    now = time.time()
    if now - _last_scan_time < _SCAN_INTERVAL:
        return
        yield

    _last_scan_time = now

    try:
        current = scan_agents(workspace=_workspace_path)
    except Exception as e:
        logger.debug(f"Periodic agent scan failed: {e}")
        return
        yield

    current_by_pid = {a.pid: a for a in current}
    current_pids = set(current_by_pid)
    known_pids = set(_known_agents)

    arrived = current_pids - known_pids
    departed = known_pids - current_pids

    if arrived:
        for pid in arrived:
            a = current_by_pid[pid]
            _known_agents[pid] = a
            if not a.stale:
                logger.info(f"New agent arrived: {a.runtime} PID {a.pid}")
                yield Message(
                    "system",
                    f"<workspace-agent-arrived>\n"
                    f"New agent in workspace: {_format_agent_line(a)}\n"
                    f"</workspace-agent-arrived>",
                )

    if departed:
        for pid in departed:
            a = _known_agents.pop(pid)
            logger.info(f"Agent departed: {a.runtime} PID {a.pid}")
            yield Message(
                "system",
                f"<workspace-agent-departed>\n"
                f"Agent left workspace: {a.runtime} PID {a.pid}\n"
                f"</workspace-agent-departed>",
            )


# ---------------------------------------------------------------------------
#  Registration
# ---------------------------------------------------------------------------


def register() -> None:
    """Register workspace agent awareness hooks."""
    register_hook(
        "workspace_agents.session_start",
        HookType.SESSION_START,
        session_start_agents,
        priority=100,  # High priority — run before other session start hooks
    )
    register_hook(
        "workspace_agents.step_pre",
        HookType.STEP_PRE,
        step_pre_agents,
        priority=10,  # Low priority — don't delay other hooks
    )
    logger.debug("Registered workspace agent awareness hooks")
