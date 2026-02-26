"""Tests for workspace agent detection and metadata extraction.

Tests the detect_runtime function for all supported agent runtimes,
exclusion patterns, edge cases, and helper functions.
"""

from gptme.hooks.workspace_agents import (
    AGENT_BINARIES,
    AgentInfo,
    _extract_flag,
    _format_agent_line,
    _format_duration,
    _has_flag,
    _parse_etime,
    assess_staleness,
    detect_runtime,
)

# ---------------------------------------------------------------------------
#  detect_runtime tests — existing runtimes
# ---------------------------------------------------------------------------


class TestDetectRuntimeExisting:
    """Tests for detection of the original 4 runtimes."""

    def test_claude_direct(self):
        assert detect_runtime(["claude"]) == "claude-code"

    def test_claude_with_flags(self):
        assert detect_runtime(["claude", "-m", "opus", "-p", "hello"]) == "claude-code"

    def test_claude_absolute_path(self):
        assert detect_runtime(["/usr/local/bin/claude"]) == "claude-code"

    def test_gptme_direct(self):
        assert detect_runtime(["gptme"]) == "gptme"

    def test_gptme_with_flags(self):
        assert detect_runtime(["gptme", "--model", "sonnet", "-n"]) == "gptme"

    def test_gptme_path(self):
        assert detect_runtime(["/home/user/.local/bin/gptme"]) == "gptme"

    def test_codex_direct(self):
        assert detect_runtime(["codex"]) == "codex"

    def test_aider_direct(self):
        assert detect_runtime(["aider"]) == "aider"

    def test_aider_with_model(self):
        assert detect_runtime(["aider", "--model", "gpt-4"]) == "aider"


# ---------------------------------------------------------------------------
#  detect_runtime tests — new runtimes (goose, opencode, amp)
# ---------------------------------------------------------------------------


class TestDetectRuntimeNew:
    """Tests for newly added agent runtimes."""

    # Goose (Block)
    def test_goose_direct(self):
        assert detect_runtime(["goose"]) == "goose"

    def test_goose_with_model(self):
        assert detect_runtime(["goose", "--model", "claude-3"]) == "goose"

    def test_goose_with_provider(self):
        assert detect_runtime(["goose", "--provider", "anthropic"]) == "goose"

    def test_goose_absolute_path(self):
        assert detect_runtime(["/usr/local/bin/goose"]) == "goose"

    def test_goosed_server(self):
        """goosed is the Goose backend/server process."""
        assert detect_runtime(["goosed"]) == "goose"

    def test_goosed_absolute_path(self):
        assert detect_runtime(["/usr/local/bin/goosed"]) == "goose"

    # OpenCode
    def test_opencode_direct(self):
        assert detect_runtime(["opencode"]) == "opencode"

    def test_opencode_with_model(self):
        assert detect_runtime(["opencode", "--model", "gpt-4"]) == "opencode"

    def test_opencode_absolute_path(self):
        assert detect_runtime(["/usr/local/bin/opencode"]) == "opencode"

    # Amp (Sourcegraph)
    def test_amp_direct(self):
        assert detect_runtime(["amp"]) == "amp"

    def test_amp_with_model(self):
        assert detect_runtime(["amp", "--model", "claude-3.5"]) == "amp"

    def test_amp_absolute_path(self):
        assert detect_runtime(["/usr/local/bin/amp"]) == "amp"


# ---------------------------------------------------------------------------
#  detect_runtime tests — cmdline pattern fallbacks
# ---------------------------------------------------------------------------


class TestDetectRuntimePatterns:
    """Tests for interpreter-wrapped invocation patterns."""

    def test_python_gptme(self):
        assert (
            detect_runtime(["python3", "/usr/bin/gptme", "--name", "test"]) == "gptme"
        )

    def test_python_claude(self):
        assert detect_runtime(["node", "/usr/bin/claude", "-p"]) == "claude-code"

    def test_python_goose(self):
        assert detect_runtime(["python3", "/usr/bin/goose", "session"]) == "goose"

    def test_python_goosed(self):
        assert detect_runtime(["python3", "/usr/bin/goosed"]) == "goose"

    def test_python_opencode(self):
        assert detect_runtime(["go", "/usr/bin/opencode"]) == "opencode"

    def test_python_amp(self):
        assert detect_runtime(["node", "/usr/bin/amp", "run"]) == "amp"


# ---------------------------------------------------------------------------
#  detect_runtime tests — exclusion patterns
# ---------------------------------------------------------------------------


class TestDetectRuntimeExclusions:
    """Tests for exclusion patterns that prevent false positives."""

    def test_exclude_shell_snapshots(self):
        """Claude Code's shell subprocess snapshots should be excluded."""
        assert (
            detect_runtime(["bash", "-c", "cat /home/user/.claude/shell-snapshots/xxx"])
            is None
        )

    def test_exclude_server_py(self):
        assert detect_runtime(["python3", "server.py"]) is None

    def test_exclude_twitter_loop(self):
        assert detect_runtime(["python3", "twitter-loop.sh"]) is None

    def test_exclude_discord_bot(self):
        assert detect_runtime(["python3", "discord_bot.py"]) is None

    def test_exclude_shell_wrapper(self):
        assert detect_runtime(["/bin/sh", "-c", "some command"]) is None

    def test_exclude_timeout_wrapper(self):
        assert detect_runtime(["/usr/bin/timeout", "60", "claude"]) is None

    def test_exclude_tee(self):
        assert detect_runtime(["/usr/bin/tee", "logfile.txt"]) is None

    def test_non_agent_binary(self):
        assert detect_runtime(["vim", "file.py"]) is None

    def test_empty_cmdline(self):
        assert detect_runtime([]) is None


# ---------------------------------------------------------------------------
#  detect_runtime tests — edge cases
# ---------------------------------------------------------------------------


class TestDetectRuntimeEdgeCases:
    """Edge cases and tricky scenarios."""

    def test_exe_suffix_stripped(self):
        """Windows-style .exe suffix should be stripped."""
        assert detect_runtime(["claude.exe", "-p", "hello"]) == "claude-code"

    def test_agent_binary_keys_match_runtimes(self):
        """Every binary in AGENT_BINARIES should produce a valid runtime."""
        for binary, expected_runtime in AGENT_BINARIES.items():
            assert detect_runtime([binary]) == expected_runtime, (
                f"Binary '{binary}' should detect as '{expected_runtime}'"
            )


# ---------------------------------------------------------------------------
#  Helper function tests
# ---------------------------------------------------------------------------


class TestExtractFlag:
    """Tests for _extract_flag helper."""

    def test_long_flag_space(self):
        assert _extract_flag(["--model", "opus"], "--model") == "opus"

    def test_long_flag_equals(self):
        assert _extract_flag(["--model=opus"], "--model") == "opus"

    def test_short_flag(self):
        assert _extract_flag(["-m", "opus"], "-m") == "opus"

    def test_multiple_flags(self):
        assert _extract_flag(["-m", "opus"], "--model", "-m") == "opus"

    def test_flag_not_found(self):
        assert _extract_flag(["--name", "test"], "--model") is None

    def test_flag_at_end(self):
        """Flag at end of cmdline with no value should return None."""
        assert _extract_flag(["--model"], "--model") is None


class TestHasFlag:
    """Tests for _has_flag helper."""

    def test_flag_present(self):
        assert _has_flag(["--non-interactive"], "--non-interactive") is True

    def test_flag_absent(self):
        assert _has_flag(["--model", "opus"], "--non-interactive") is False

    def test_any_flag(self):
        assert _has_flag(["-n"], "-n", "--non-interactive") is True


class TestParseEtime:
    """Tests for _parse_etime (ps etime/cputime format parser)."""

    def test_seconds(self):
        assert _parse_etime("45") == 45

    def test_minutes_seconds(self):
        assert _parse_etime("05:30") == 330

    def test_hours_minutes_seconds(self):
        assert _parse_etime("02:05:30") == 7530

    def test_days(self):
        assert _parse_etime("3-02:05:30") == 266730

    def test_empty(self):
        assert _parse_etime("") is None

    def test_decimal_seconds(self):
        assert _parse_etime("45.5") == 45


class TestFormatDuration:
    """Tests for _format_duration."""

    def test_seconds(self):
        assert _format_duration(45) == "45s"

    def test_minutes(self):
        assert _format_duration(300) == "5m"

    def test_hours(self):
        assert _format_duration(7500) == "2h05m"

    def test_days(self):
        assert _format_duration(90000) == "1d1h"


# ---------------------------------------------------------------------------
#  Staleness assessment tests
# ---------------------------------------------------------------------------


class TestAssessStaleness:
    """Tests for assess_staleness heuristics."""

    def test_zombie_always_stale(self):
        agent = AgentInfo(pid=1, runtime="gptme", cwd="/tmp", process_state="Z")
        agent.uptime_seconds = 10
        assess_staleness(agent)
        assert agent.stale is True
        assert "zombie" in (agent.stale_reason or "")

    def test_stopped_always_stale(self):
        agent = AgentInfo(pid=1, runtime="gptme", cwd="/tmp", process_state="T")
        agent.uptime_seconds = 10
        assess_staleness(agent)
        assert agent.stale is True
        assert "stopped" in (agent.stale_reason or "")

    def test_fresh_not_stale(self):
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=60,
            cpu_seconds=5.0,
            process_state="S",
        )
        assess_staleness(agent)
        assert agent.stale is False

    def test_old_low_cpu_stale(self):
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="autonomous",
            uptime_seconds=10000,
            cpu_seconds=0.5,
            process_state="S",
        )
        assess_staleness(agent)
        assert agent.stale is True

    def test_server_never_stale(self):
        agent = AgentInfo(
            pid=1,
            runtime="gptme",
            cwd="/tmp",
            mode="server",
            uptime_seconds=999999,
            cpu_seconds=0.1,
            process_state="S",
        )
        assess_staleness(agent)
        assert agent.stale is False

    def test_no_uptime_not_stale(self):
        agent = AgentInfo(pid=1, runtime="gptme", cwd="/tmp")
        assess_staleness(agent)
        assert agent.stale is False


# ---------------------------------------------------------------------------
#  Format agent line tests
# ---------------------------------------------------------------------------


class TestFormatAgentLine:
    """Tests for _format_agent_line output formatting."""

    def test_basic(self):
        agent = AgentInfo(pid=123, runtime="claude-code", cwd="/workspace")
        line = _format_agent_line(agent)
        assert "PID 123" in line
        assert "claude-code" in line

    def test_with_metadata(self):
        agent = AgentInfo(
            pid=456,
            runtime="gptme",
            cwd="/workspace",
            model="sonnet",
            mode="autonomous",
            branch="feature",
        )
        line = _format_agent_line(agent)
        assert "model=sonnet" in line
        assert "autonomous" in line
        assert "branch=feature" in line

    def test_stale_flag(self):
        agent = AgentInfo(pid=789, runtime="aider", cwd="/workspace", stale=True)
        line = _format_agent_line(agent)
        assert "[STALE]" in line

    def test_uptime_shown(self):
        agent = AgentInfo(
            pid=101,
            runtime="goose",
            cwd="/workspace",
            uptime_seconds=3600,
        )
        line = _format_agent_line(agent)
        assert "up 1h00m" in line

    def test_new_runtimes_format(self):
        """New runtimes should format correctly."""
        for runtime in ("goose", "opencode", "amp"):
            agent = AgentInfo(pid=100, runtime=runtime, cwd="/workspace")
            line = _format_agent_line(agent)
            assert runtime in line


# ---------------------------------------------------------------------------
#  Parser tests for new runtimes
# ---------------------------------------------------------------------------


class TestNewRuntimeParsers:
    """Tests for the new runtime metadata parsers."""

    def test_goose_parser_basic(self):
        from gptme.hooks.workspace_agents import _parse_goose

        info = _parse_goose(100, ["goose", "session"], "/workspace")
        assert info.runtime == "goose"
        assert info.mode == "unknown"

    def test_goose_parser_with_model(self):
        from gptme.hooks.workspace_agents import _parse_goose

        info = _parse_goose(100, ["goose", "--model", "claude-3"], "/workspace")
        assert info.model == "claude-3"

    def test_goose_parser_with_provider(self):
        from gptme.hooks.workspace_agents import _parse_goose

        info = _parse_goose(100, ["goose", "--provider", "anthropic"], "/workspace")
        assert info.extra.get("provider") == "anthropic"

    def test_goosed_is_server(self):
        from gptme.hooks.workspace_agents import _parse_goose

        info = _parse_goose(100, ["goosed"], "/workspace")
        assert info.mode == "server"

    def test_opencode_parser_basic(self):
        from gptme.hooks.workspace_agents import _parse_opencode

        info = _parse_opencode(100, ["opencode"], "/workspace")
        assert info.runtime == "opencode"

    def test_opencode_parser_with_model(self):
        from gptme.hooks.workspace_agents import _parse_opencode

        info = _parse_opencode(100, ["opencode", "-m", "gpt-4"], "/workspace")
        assert info.model == "gpt-4"

    def test_amp_parser_basic(self):
        from gptme.hooks.workspace_agents import _parse_amp

        info = _parse_amp(100, ["amp"], "/workspace")
        assert info.runtime == "amp"

    def test_amp_parser_with_model(self):
        from gptme.hooks.workspace_agents import _parse_amp

        info = _parse_amp(100, ["amp", "--model", "claude-sonnet"], "/workspace")
        assert info.model == "claude-sonnet"
