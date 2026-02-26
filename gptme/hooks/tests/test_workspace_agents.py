"""Tests for workspace_agents hook (process-based parallel agent detection)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.hooks.workspace_agents import (
    AGENT_BINARIES,
    AgentInfo,
    _extract_flag,
    _format_duration,
    _has_flag,
    _init_tracking,
    _parse_etime,
    assess_staleness,
    detect_runtime,
    scan_agents,
    session_start_agents,
    step_pre_agents,
)
from gptme.message import Message

# ---------------------------------------------------------------------------
#  Runtime detection
# ---------------------------------------------------------------------------


class TestDetectRuntime:
    """Tests for detect_runtime — identifying agent runtimes from cmdline."""

    def test_gptme_direct(self) -> None:
        assert detect_runtime(["/usr/bin/gptme", "--model", "opus"]) == "gptme"

    def test_gptme_python_wrapped(self) -> None:
        assert (
            detect_runtime(["python3", "/home/bob/.local/bin/gptme", "-n"]) == "gptme"
        )

    def test_claude_code(self) -> None:
        assert detect_runtime(["claude", "-p", "do stuff"]) == "claude-code"

    def test_codex(self) -> None:
        assert detect_runtime(["codex", "--model", "o3"]) == "codex"

    def test_aider(self) -> None:
        assert detect_runtime(["aider", "--model", "sonnet"]) == "aider"

    def test_unrelated_process(self) -> None:
        assert detect_runtime(["vim", "file.py"]) is None

    def test_shell_wrapper_excluded(self) -> None:
        assert detect_runtime(["bash", "-c", "claude -p 'hello'"]) is None

    def test_cc_snapshot_excluded(self) -> None:
        assert (
            detect_runtime(
                ["/bin/bash", "--rcfile", "/home/x/.claude/shell-snapshots/foo.sh"]
            )
            is None
        )

    def test_server_py_excluded(self) -> None:
        assert detect_runtime(["python3", "server.py", "--host", "0.0.0.0"]) is None

    def test_timeout_wrapper_excluded(self) -> None:
        assert detect_runtime(["/usr/bin/timeout", "600", "claude", "-p", "x"]) is None

    def test_binary_exe_suffix(self) -> None:
        """Windows .exe suffix should be stripped."""
        assert detect_runtime(["claude.exe", "-p", "hello"]) == "claude-code"

    def test_all_known_binaries_detected(self) -> None:
        """Every entry in AGENT_BINARIES should be detected."""
        for binary, runtime in AGENT_BINARIES.items():
            assert detect_runtime([binary]) == runtime, f"Failed for {binary}"


# ---------------------------------------------------------------------------
#  Flag extraction
# ---------------------------------------------------------------------------


class TestExtractFlag:
    def test_space_separated(self) -> None:
        assert _extract_flag(["cmd", "--model", "opus"], "--model") == "opus"

    def test_equals_syntax(self) -> None:
        assert _extract_flag(["cmd", "--model=opus"], "--model") == "opus"

    def test_missing(self) -> None:
        assert _extract_flag(["cmd", "--verbose"], "--model") is None

    def test_short_flag(self) -> None:
        assert _extract_flag(["cmd", "-m", "sonnet"], "-m") == "sonnet"

    def test_multiple_flags(self) -> None:
        assert _extract_flag(["cmd", "-v"], "--model", "-m") is None
        assert _extract_flag(["cmd", "-m", "opus"], "--model", "-m") == "opus"


class TestHasFlag:
    def test_present(self) -> None:
        assert _has_flag(["cmd", "-p", "text"], "-p", "--print") is True

    def test_absent(self) -> None:
        assert _has_flag(["cmd", "text"], "-p", "--print") is False


# ---------------------------------------------------------------------------
#  Time parsing & formatting
# ---------------------------------------------------------------------------


class TestParseEtime:
    def test_minutes_seconds(self) -> None:
        assert _parse_etime("05:30") == 330

    def test_hours_minutes_seconds(self) -> None:
        assert _parse_etime("01:05:30") == 3930

    def test_days_hours_minutes_seconds(self) -> None:
        assert _parse_etime("2-01:05:30") == 2 * 86400 + 3930

    def test_fractional_seconds(self) -> None:
        assert _parse_etime("00:03.42") == 3

    def test_empty(self) -> None:
        assert _parse_etime("") is None

    def test_invalid(self) -> None:
        assert _parse_etime("abc") is None


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert _format_duration(45) == "45s"

    def test_minutes(self) -> None:
        assert _format_duration(300) == "5m"

    def test_hours(self) -> None:
        assert _format_duration(7200) == "2h00m"

    def test_days(self) -> None:
        assert _format_duration(90000) == "1d1h"


# ---------------------------------------------------------------------------
#  Staleness assessment
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_fresh_process_not_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=60,
            cpu_seconds=5.0,
        )
        assess_staleness(agent)
        assert agent.stale is False

    def test_zombie_always_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="interactive",
            uptime_seconds=10,
            process_state="Z",
        )
        assess_staleness(agent)
        assert agent.stale is True
        assert "zombie" in (agent.stale_reason or "")

    def test_stopped_always_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="interactive",
            uptime_seconds=10,
            process_state="T",
        )
        assess_staleness(agent)
        assert agent.stale is True
        assert "stopped" in (agent.stale_reason or "")

    def test_old_low_cpu_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="claude-code",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=10000,
            cpu_seconds=0.5,
        )
        assess_staleness(agent)
        assert agent.stale is True
        assert "utilization" in (agent.stale_reason or "")

    def test_old_high_cpu_not_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="claude-code",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=10000,
            cpu_seconds=500.0,
        )
        assess_staleness(agent)
        assert agent.stale is False

    def test_server_never_stale(self) -> None:
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="server",
            uptime_seconds=999999,
            cpu_seconds=1.0,
        )
        assess_staleness(agent)
        assert agent.stale is False

    def test_no_uptime_no_assessment(self) -> None:
        agent = AgentInfo(pid=1, runtime="gptme", cwd="/tmp")
        assess_staleness(agent)
        assert agent.stale is False

    def test_extremely_old_stale_regardless_of_cpu(self) -> None:
        """3x beyond threshold → stale even with some CPU activity."""
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=25000,
            cpu_seconds=100.0,
        )
        assess_staleness(agent)
        assert agent.stale is True
        assert "3x beyond" in (agent.stale_reason or "")


# ---------------------------------------------------------------------------
#  Scan agents (with mocking)
# ---------------------------------------------------------------------------


class TestScanAgents:
    def test_empty_when_no_agents(self) -> None:
        with patch("gptme.hooks.workspace_agents._get_all_pids", return_value=[]):
            assert scan_agents() == []

    def test_skips_own_pid(self) -> None:
        my_pid = os.getpid()
        with (
            patch("gptme.hooks.workspace_agents._get_all_pids", return_value=[my_pid]),
            patch(
                "gptme.hooks.workspace_agents._get_process_cmdline",
                return_value=["gptme"],
            ),
        ):
            assert scan_agents() == []

    def test_detects_gptme_in_workspace(self) -> None:
        fake_pid = 99990
        workspace = "/home/bob/project"

        with (
            patch(
                "gptme.hooks.workspace_agents._get_all_pids", return_value=[fake_pid]
            ),
            patch(
                "gptme.hooks.workspace_agents._get_process_cmdline",
                return_value=["gptme", "--model", "opus", "-n"],
            ),
            patch(
                "gptme.hooks.workspace_agents._get_process_cwd", return_value=workspace
            ),
            patch("gptme.hooks.workspace_agents._get_git_branch", return_value="main"),
            patch(
                "gptme.hooks.workspace_agents._get_process_timing",
                return_value=(120, 5.0, "S"),
            ),
            patch("os.path.realpath", side_effect=lambda p: p),
        ):
            agents = scan_agents(workspace=workspace)
            assert len(agents) == 1
            assert agents[0].runtime == "gptme"
            assert agents[0].model == "opus"
            assert agents[0].mode == "autonomous"
            assert agents[0].branch == "main"

    def test_filters_by_workspace(self) -> None:
        fake_pid = 99991

        with (
            patch(
                "gptme.hooks.workspace_agents._get_all_pids", return_value=[fake_pid]
            ),
            patch(
                "gptme.hooks.workspace_agents._get_process_cmdline",
                return_value=["claude", "-p", "hello"],
            ),
            patch(
                "gptme.hooks.workspace_agents._get_process_cwd",
                return_value="/other/project",
            ),
            patch("os.path.realpath", side_effect=lambda p: p),
        ):
            agents = scan_agents(workspace="/home/bob/project")
            assert len(agents) == 0


# ---------------------------------------------------------------------------
#  Session start hook
# ---------------------------------------------------------------------------


class TestSessionStartHook:
    def test_no_workspace(self) -> None:
        msgs = list(session_start_agents(Path("/tmp/log"), None, []))
        assert len(msgs) == 0

    def test_no_agents_found(self) -> None:
        with patch("gptme.hooks.workspace_agents.scan_agents", return_value=[]):
            msgs = list(session_start_agents(Path("/tmp/log"), Path("/workspace"), []))
            assert len(msgs) == 0

    def test_warns_on_active_agents(self) -> None:
        fake_agent = AgentInfo(
            pid=12345,
            runtime="claude-code",
            cwd="/workspace",
            model="opus",
            mode="autonomous",
            branch="main",
        )
        with patch(
            "gptme.hooks.workspace_agents.scan_agents", return_value=[fake_agent]
        ):
            msgs = list(session_start_agents(Path("/tmp/log"), Path("/workspace"), []))
            assert len(msgs) == 1
            assert isinstance(msgs[0], Message)
            assert "agent(s) detected" in msgs[0].content
            assert "claude-code" in msgs[0].content
            assert "worktree" in msgs[0].content

    def test_stale_agents_noted_separately(self) -> None:
        stale_agent = AgentInfo(
            pid=11111,
            runtime="gptme",
            cwd="/workspace",
            mode="autonomous",
            stale=True,
            stale_reason="zombie",
        )
        with patch(
            "gptme.hooks.workspace_agents.scan_agents", return_value=[stale_agent]
        ):
            msgs = list(session_start_agents(Path("/tmp/log"), Path("/workspace"), []))
            assert len(msgs) == 1
            assert isinstance(msgs[0], Message)
            assert "stale" in msgs[0].content.lower()

    def test_scan_failure_handled_gracefully(self) -> None:
        with patch(
            "gptme.hooks.workspace_agents.scan_agents",
            side_effect=PermissionError("no /proc access"),
        ):
            msgs = list(session_start_agents(Path("/tmp/log"), Path("/workspace"), []))
            assert len(msgs) == 0


# ---------------------------------------------------------------------------
#  Periodic monitoring (step_pre hook)
# ---------------------------------------------------------------------------


class TestStepPreAgents:
    """Tests for step_pre_agents — periodic arrival/departure detection."""

    @pytest.fixture(autouse=True)
    def reset_tracking(self) -> None:  # type: ignore[return]
        """Reset module-level tracking state between tests."""
        import gptme.hooks.workspace_agents as mod

        mod._known_agents = {}
        mod._workspace_path = None
        mod._last_scan_time = 0.0
        return

    def _make_manager(self) -> object:
        """Create a minimal mock LogManager."""
        from types import SimpleNamespace

        return SimpleNamespace(workspace=Path("/workspace"))

    def test_no_workspace_no_scan(self) -> None:
        """If workspace was never set (e.g. no session_start), no scan happens."""
        msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
        assert len(msgs) == 0

    def test_throttled_no_repeat(self) -> None:
        """Scans are throttled — won't rescan within _SCAN_INTERVAL."""
        import time

        _init_tracking("/workspace", [])

        import gptme.hooks.workspace_agents as mod

        mod._last_scan_time = time.time()  # Just scanned

        with patch("gptme.hooks.workspace_agents.scan_agents") as mock_scan:
            msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
            mock_scan.assert_not_called()
            assert len(msgs) == 0

    def test_detects_new_arrival(self) -> None:
        """A new agent appearing triggers an arrival message."""
        _init_tracking("/workspace", [])

        import gptme.hooks.workspace_agents as mod

        mod._last_scan_time = 0.0  # Force rescan

        new_agent = AgentInfo(
            pid=55555,
            runtime="claude-code",
            cwd="/workspace",
            model="opus",
            mode="autonomous",
        )
        with patch(
            "gptme.hooks.workspace_agents.scan_agents", return_value=[new_agent]
        ):
            msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
            assert len(msgs) == 1
            assert isinstance(msgs[0], Message)
            assert "arrived" in msgs[0].content.lower()
            assert "claude-code" in msgs[0].content

    def test_detects_departure(self) -> None:
        """An agent disappearing triggers a departure message."""
        existing = AgentInfo(
            pid=44444,
            runtime="gptme",
            cwd="/workspace",
            mode="interactive",
        )
        _init_tracking("/workspace", [existing])

        import gptme.hooks.workspace_agents as mod

        mod._last_scan_time = 0.0  # Force rescan

        with patch("gptme.hooks.workspace_agents.scan_agents", return_value=[]):
            msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
            assert len(msgs) == 1
            assert isinstance(msgs[0], Message)
            assert "departed" in msgs[0].content.lower()
            assert "gptme" in msgs[0].content

    def test_stale_arrival_suppressed(self) -> None:
        """Stale agents arriving don't produce messages."""
        _init_tracking("/workspace", [])

        import gptme.hooks.workspace_agents as mod

        mod._last_scan_time = 0.0

        stale = AgentInfo(
            pid=66666,
            runtime="aider",
            cwd="/workspace",
            stale=True,
            stale_reason="zombie",
        )
        with patch("gptme.hooks.workspace_agents.scan_agents", return_value=[stale]):
            msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
            # Stale arrivals are tracked but don't produce messages
            assert len(msgs) == 0

    def test_scan_failure_in_periodic(self) -> None:
        """Periodic scan failures are handled gracefully."""
        _init_tracking("/workspace", [])

        import gptme.hooks.workspace_agents as mod

        mod._last_scan_time = 0.0

        with patch(
            "gptme.hooks.workspace_agents.scan_agents",
            side_effect=OSError("permission denied"),
        ):
            msgs = list(step_pre_agents(self._make_manager()))  # type: ignore[arg-type]
            assert len(msgs) == 0
