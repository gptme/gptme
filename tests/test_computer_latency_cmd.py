"""Tests for `gptme-util computer latency` (cmd_computer.py).

Unit-tests the latency CLI command without requiring a real X11 display.
The transport is monkey-patched to return pre-made screenshot paths.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from gptme.cli.cmd_computer import latency_cmd


def _make_transport_mock(tmp_path: Path, shot_duration_s: float = 0.05) -> MagicMock:
    """Return a mock transport whose screenshot() sleeps briefly then writes a PNG."""

    def _fake_screenshot(width: int = 0, height: int = 0) -> Path:
        time.sleep(shot_duration_s)
        p = tmp_path / f"shot_{time.monotonic():.6f}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        return p

    transport = MagicMock()
    transport.screenshot.side_effect = _fake_screenshot
    return transport


class TestLatencyCmd:
    """Tests for `gptme-util computer latency`."""

    def test_help_text(self):
        runner = CliRunner()
        result = runner.invoke(latency_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--shots" in result.output
        assert "--json" in result.output

    def test_no_display_exits_1(self, monkeypatch):
        """When no transport is available, exit with code 1."""
        monkeypatch.delenv("DISPLAY", raising=False)
        with (
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("platform.system", return_value="Linux"),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, [])
        assert result.exit_code == 1
        assert (
            "display" in result.output.lower()
            or "display"
            in result.stderr_bytes.decode("utf-8", errors="replace").lower()
        )

    def test_basic_output_has_summary(self, tmp_path, monkeypatch):
        """Default run prints per-shot lines and a summary."""
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.01)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "3"])

        assert result.exit_code == 0, result.output
        assert "median" in result.output
        assert "min" in result.output
        assert "max" in result.output

    def test_json_output_structure(self, tmp_path):
        """--json produces valid JSON with the expected keys."""
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.01)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "3", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["shots"] == 3
        assert data["successful"] == 3
        assert data["errors"] == 0
        assert "min_ms" in data
        assert "median_ms" in data
        assert "max_ms" in data
        assert "mean_ms" in data
        assert "stdev_ms" in data

    def test_json_values_are_non_negative(self, tmp_path):
        """All latency values in JSON output must be >= 0."""
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.02)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "4", "--json"])

        data = json.loads(result.output)
        for key in ("min_ms", "median_ms", "max_ms", "mean_ms", "stdev_ms"):
            assert data[key] >= 0.0, f"{key} = {data[key]}"

    def test_shots_flag_respected(self, tmp_path):
        """--shots controls the number of measurements taken."""
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.005)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "7", "--json"])

        data = json.loads(result.output)
        assert data["shots"] == 7
        assert data["successful"] == 7
        # transport.screenshot is called once for warm-up + 7 for measurements
        assert transport.screenshot.call_count == 8

    def test_shot_failures_counted(self, tmp_path):
        """Screenshot errors increment 'errors' counter and are not counted as successes."""
        call_count = {"n": 0}

        def _failing_screenshot(width: int = 0, height: int = 0) -> Path:
            call_count["n"] += 1
            if call_count["n"] > 1:  # first call (warm-up) succeeds
                raise RuntimeError("X11 error: display not responding")
            p = tmp_path / "warmup.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            return p

        transport = MagicMock()
        transport.screenshot.side_effect = _failing_screenshot

        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "3", "--json"])

        # all 3 measured shots failed → exit 1
        assert result.exit_code == 1

    def test_partial_shot_failures_reported_without_failing(self, tmp_path):
        """Partial screenshot failures are counted while successful shots are reported."""
        call_count = {"n": 0}

        def _partially_failing_screenshot(width: int = 0, height: int = 0) -> Path:
            call_count["n"] += 1
            if call_count["n"] in {3, 5}:  # warm-up succeeds; 2 measured shots fail
                raise RuntimeError("X11 error: display not responding")
            p = tmp_path / f"shot_{call_count['n']}.png"
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
            return p

        transport = MagicMock()
        transport.screenshot.side_effect = _partially_failing_screenshot

        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "5", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["shots"] == 5
        assert data["successful"] == 3
        assert data["errors"] == 2

    def test_single_successful_shot_has_null_stdev(self, tmp_path):
        """A single sample has no sample standard deviation."""
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.001)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "1", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["successful"] == 1
        assert data["stdev_ms"] is None

    def test_display_override_is_restored(self, tmp_path, monkeypatch):
        """--display only changes DISPLAY for the duration of the command."""
        monkeypatch.setenv("DISPLAY", ":old")
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.001)

        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(
                latency_cmd, ["--shots", "1", "--display", ":new", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["display"] == ":new"
        assert os.environ["DISPLAY"] == ":old"

    def test_invalid_shots_exits_1(self):
        """--shots 0 exits with code 1 and an error message."""
        runner = CliRunner()
        result = runner.invoke(latency_cmd, ["--shots", "0"])
        assert result.exit_code != 0

    def test_healthy_latency_message(self, tmp_path):
        """Fast screenshots (< 100 ms) produce a 'healthy' message."""
        # Use a very short duration so it's definitely < 100 ms
        transport = _make_transport_mock(tmp_path, shot_duration_s=0.001)
        with patch(
            "gptme.tools.computer_transport.get_transport", return_value=transport
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "3"])

        assert result.exit_code == 0
        # Either health tag should appear
        assert any(marker in result.output for marker in ("✓", "⚠", "✗"))

    def test_autodetects_native_transport_when_display_set(self, tmp_path, monkeypatch):
        """When get_transport() returns None but DISPLAY is set, auto-use NativeComputerTransport.

        Fixes #216: previously the latency command failed with "no display available"
        even when X11 was running, unless GPTME_COMPUTER_TRANSPORT=native was set explicitly.
        """
        monkeypatch.setenv("DISPLAY", ":1")
        native_transport = _make_transport_mock(tmp_path, shot_duration_s=0.001)

        with (
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("platform.system", return_value="Linux"),
            patch(
                "gptme.tools.computer_transport.NativeComputerTransport",
                return_value=native_transport,
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "1", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["successful"] == 1

    def test_no_display_no_transport_exits_1(self, monkeypatch):
        """No DISPLAY + no transport → exits with an actionable error."""
        monkeypatch.delenv("DISPLAY", raising=False)
        with (
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("platform.system", return_value="Linux"),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, [])
        assert result.exit_code == 1


class TestLatencyTerminalFlag:
    """Tests for ``--terminal`` flag in ``gptme-util computer latency``.

    These tests verify the terminal startup measurement added to address the
    "figure out what is causing the delays" item from gptme/gptme#216.

    All subprocess calls are patched so tests run without a real X11 display.
    """

    def _make_screenshot_transport(self, tmp_path: Path) -> MagicMock:
        """Minimal transport mock that returns a valid PNG for each screenshot."""
        transport = MagicMock()
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        def _fake_shot(width: int = 0, height: int = 0) -> Path:
            p = tmp_path / "shot.png"
            p.write_bytes(png_bytes)
            return p

        transport.screenshot.side_effect = _fake_shot
        return transport

    def test_terminal_flag_on_non_linux_warns(self, tmp_path, monkeypatch):
        """On non-Linux, --terminal emits a warning instead of failing hard."""
        transport = self._make_screenshot_transport(tmp_path)
        monkeypatch.setenv("DISPLAY", ":1")
        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("platform.system", return_value="Darwin"),
            patch("sys.platform", "darwin"),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0
        assert (
            "only supported on Linux" in result.output
            or "terminal" in result.output.lower()
        )

    def test_terminal_flag_without_display_skips_measurement(
        self, tmp_path, monkeypatch
    ):
        """When DISPLAY is unset, --terminal skips the measurement with a warning."""
        transport = self._make_screenshot_transport(tmp_path)
        monkeypatch.delenv("DISPLAY", raising=False)
        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("platform.system", return_value="Linux"),
            patch("sys.platform", "linux"),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0
        assert "DISPLAY" in result.output or "terminal" in result.output.lower()

    def test_terminal_json_includes_terminal_startup_key(self, tmp_path, monkeypatch):
        """With --terminal and --json, result includes 'terminal_startup' key."""
        transport = self._make_screenshot_transport(tmp_path)
        monkeypatch.setenv("DISPLAY", ":1")

        fake_startup = {
            "terminal": "xterm",
            "args": ["-fn", "fixed"],
            "startup_ms": 120,
            "display": ":1",
        }

        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("platform.system", return_value="Linux"),
            patch("sys.platform", "linux"),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=fake_startup,
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                latency_cmd, ["--shots", "1", "--terminal", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "terminal_startup" in data
        assert data["terminal_startup"]["startup_ms"] == 120
        assert data["terminal_startup"]["terminal"] == "xterm"

    def test_terminal_json_error_propagated(self, tmp_path, monkeypatch):
        """When terminal launch fails, error is included in 'terminal_startup' key."""
        transport = self._make_screenshot_transport(tmp_path)
        monkeypatch.setenv("DISPLAY", ":1")

        fake_error = {
            "error": "no terminal emulator found — install xterm: sudo apt install xterm"
        }

        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("platform.system", return_value="Linux"),
            patch("sys.platform", "linux"),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=fake_error,
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(
                latency_cmd, ["--shots", "1", "--terminal", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "terminal_startup" in data
        assert "error" in data["terminal_startup"]
        assert "xterm" in data["terminal_startup"]["error"]

    def test_terminal_text_output_shows_startup_time(self, tmp_path, monkeypatch):
        """Non-JSON --terminal output shows the terminal name and startup time."""
        transport = self._make_screenshot_transport(tmp_path)
        monkeypatch.setenv("DISPLAY", ":1")

        fake_startup = {
            "terminal": "xterm",
            "args": ["-fn", "fixed"],
            "startup_ms": 150,
            "display": ":1",
        }

        with (
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("platform.system", return_value="Linux"),
            patch("sys.platform", "linux"),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=fake_startup,
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0, result.output
        assert "150" in result.output  # startup_ms
        assert "xterm" in result.output

    def test_measure_terminal_startup_no_xterm(self):
        """_measure_terminal_startup returns error dict when no terminal is found."""
        from gptme.cli.cmd_computer import _measure_terminal_startup

        with patch("shutil.which", return_value=None):
            result = _measure_terminal_startup(":1")

        assert "error" in result
        assert "terminal" in result["error"].lower() or "xterm" in result["error"]

    def test_measure_terminal_startup_no_xdotool(self):
        """_measure_terminal_startup returns error dict when xdotool is absent."""
        from gptme.cli.cmd_computer import _measure_terminal_startup

        def _which(cmd: str) -> str | None:
            if cmd == "xterm":
                return "/usr/bin/xterm"
            return None  # xdotool not found

        with patch("shutil.which", side_effect=_which):
            result = _measure_terminal_startup(":1")

        assert "error" in result
        assert "xdotool" in result["error"]

    def test_measure_terminal_startup_xdotool_timeout(self):
        """_measure_terminal_startup returns error dict when xdotool --sync times out."""
        import subprocess as _subprocess

        from gptme.cli.cmd_computer import _measure_terminal_startup

        fake_proc = MagicMock()
        fake_proc.pid = 99999

        def _which(cmd: str) -> str | None:
            return f"/usr/bin/{cmd}" if cmd in ("xterm", "xdotool") else None

        with (
            patch("shutil.which", side_effect=_which),
            patch("subprocess.Popen", return_value=fake_proc),
            patch(
                "subprocess.run",
                side_effect=_subprocess.TimeoutExpired(cmd=["xdotool"], timeout=5.0),
            ),
        ):
            result = _measure_terminal_startup(":1", timeout=5.0)

        assert "error" in result
        assert "xterm" in result.get("terminal", "") or "terminal" in result["error"]
        assert (
            "did not appear" in result["error"] or "timeout" in result["error"].lower()
        )

    def test_measure_terminal_startup_xdotool_called_process_error(self):
        """_measure_terminal_startup returns error dict when xdotool exits non-zero."""
        import subprocess as _subprocess

        from gptme.cli.cmd_computer import _measure_terminal_startup

        fake_proc = MagicMock()
        fake_proc.pid = 99999

        def _which(cmd: str) -> str | None:
            return f"/usr/bin/{cmd}" if cmd in ("xterm", "xdotool") else None

        with (
            patch("shutil.which", side_effect=_which),
            patch("subprocess.Popen", return_value=fake_proc),
            patch(
                "subprocess.run",
                side_effect=_subprocess.CalledProcessError(
                    returncode=1,
                    cmd=["xdotool", "search"],
                    stderr="no windows found",
                ),
            ),
        ):
            result = _measure_terminal_startup(":1", timeout=5.0)

        assert "error" in result
        assert "xdotool" in result["error"]

    def test_measure_terminal_startup_returns_startup_ms_on_success(self, tmp_path):
        """_measure_terminal_startup returns startup_ms (int) on a successful launch."""
        import subprocess as _subprocess

        from gptme.cli.cmd_computer import _measure_terminal_startup

        fake_proc = MagicMock()
        fake_proc.pid = 99999

        def _which(cmd: str) -> str | None:
            return f"/usr/bin/{cmd}" if cmd in ("xterm", "xdotool") else None

        with (
            patch("shutil.which", side_effect=_which),
            patch("subprocess.Popen", return_value=fake_proc),
            patch(
                "subprocess.run",
                return_value=_subprocess.CompletedProcess([], 0, "", ""),
            ),
        ):
            result = _measure_terminal_startup(":1", timeout=5.0)

        assert "startup_ms" in result, f"Expected startup_ms in {result}"
        assert isinstance(result["startup_ms"], int)
        assert result["startup_ms"] >= 0
        assert result["terminal"] == "xterm"
        assert result["display"] == ":1"
