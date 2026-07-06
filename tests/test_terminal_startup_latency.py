"""Tests for terminal window startup latency measurement (gptme/gptme#216).

Unit-tests _measure_terminal_startup() and the --terminal flag of
`gptme-util computer latency` without requiring a real X display or xdotool.
All subprocess calls are monkey-patched.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from gptme.cli.cmd_computer import _measure_terminal_startup

# ---------------------------------------------------------------------------
# Unit tests for _measure_terminal_startup()
# ---------------------------------------------------------------------------


class TestMeasureTerminalStartup:
    """Unit tests for _measure_terminal_startup().

    _measure_terminal_startup does `import subprocess` locally inside the
    function, so we patch subprocess.Popen / subprocess.run at the subprocess
    module level (the canonical way to mock them).
    """

    def test_returns_startup_ms_on_success(self):
        """Happy path: xterm and xdotool both available, window appears."""

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen") as mock_popen,
            patch("subprocess.run", side_effect=fake_run),
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            result = _measure_terminal_startup(":1")

        assert "error" not in result, f"unexpected error: {result.get('error')}"
        assert "startup_ms" in result
        assert isinstance(result["startup_ms"], int)
        assert result["startup_ms"] >= 0
        assert result["terminal"] == "xterm"
        assert result["display"] == ":1"

    def test_returns_error_when_no_terminal_found(self):
        """No terminal emulator installed → error dict."""
        with patch("shutil.which", return_value=None):
            result = _measure_terminal_startup(":1")

        assert "error" in result
        assert "xterm" in result["error"]

    def test_returns_error_when_xdotool_missing(self):
        """xterm is available but xdotool is not → error dict."""

        def which_side(name):
            return "/usr/bin/xterm" if name == "xterm" else None

        with patch("shutil.which", side_effect=which_side):
            result = _measure_terminal_startup(":1")

        assert "error" in result
        assert "xdotool" in result["error"]

    def test_returns_error_on_timeout(self):
        """xdotool times out waiting for window → error with timeout message."""

        def fake_run_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 15.0)

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen") as mock_popen,
            patch("subprocess.run", side_effect=fake_run_timeout),
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            result = _measure_terminal_startup(":1", timeout=15.0)

        assert "error" in result

    def test_returns_error_on_xdotool_failure(self):
        """xdotool search exits non-zero → error dict."""

        def fake_run_fail(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, "", "no windows found")

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen") as mock_popen,
            patch("subprocess.run", side_effect=fake_run_fail),
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            result = _measure_terminal_startup(":1")

        assert "error" in result

    def test_process_is_terminated_on_success(self):
        """Launched xterm process is always cleaned up."""

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen") as mock_popen,
            patch("subprocess.run", side_effect=fake_run),
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            _measure_terminal_startup(":1")

        mock_proc.terminate.assert_called_once()

    def test_process_is_terminated_on_timeout(self):
        """Launched xterm process is cleaned up even when xdotool times out."""

        def fake_run_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 15.0)

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen") as mock_popen,
            patch("subprocess.run", side_effect=fake_run_timeout),
        ):
            mock_proc = MagicMock()
            mock_popen.return_value = mock_proc

            _measure_terminal_startup(":1")

        mock_proc.terminate.assert_called_once()

    def test_bitmap_font_args_used_first(self):
        """xterm -fn fixed (bitmap font) is tried first to avoid font scan."""
        captured_cmds: list[list] = []

        def fake_popen(cmd, **kwargs):
            captured_cmds.append(cmd)
            return MagicMock()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch("shutil.which", return_value="/usr/bin/xterm"),
            patch("subprocess.Popen", side_effect=fake_popen),
            patch("subprocess.run", side_effect=fake_run),
        ):
            _measure_terminal_startup(":1")

        assert captured_cmds, "xterm should have been launched"
        assert "-fn" in captured_cmds[0], "First attempt should use -fn bitmap flag"
        assert "fixed" in captured_cmds[0], (
            "First attempt should use 'fixed' bitmap font"
        )


# ---------------------------------------------------------------------------
# Integration tests for latency_cmd --terminal flag
# ---------------------------------------------------------------------------


class TestLatencyCmdTerminalFlag:
    """Tests for `gptme-util computer latency --terminal`."""

    def _make_mock_transport(self):
        """Return a transport mock whose screenshot() returns a tiny valid PNG."""
        import tempfile
        from pathlib import Path

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        # Minimal 1×1 RGB PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd4d"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        tmp.write(png_bytes)
        tmp.close()

        transport = MagicMock()
        transport.screenshot.return_value = Path(tmp.name)
        return transport

    def test_terminal_flag_skipped_on_non_linux(self):
        """On non-Linux platforms, --terminal flag emits a warning but still exits 0."""
        from gptme.cli.cmd_computer import latency_cmd

        runner = CliRunner()
        transport = self._make_mock_transport()

        with (
            patch("sys.platform", "darwin"),
            patch("platform.system", return_value="Darwin"),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                side_effect=AssertionError("should not be called on non-Linux"),
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=transport,
            ),
        ):
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0

    def test_terminal_flag_included_in_json_output(self):
        """With --json --terminal, terminal_startup key appears in JSON output."""
        import json

        from gptme.cli.cmd_computer import latency_cmd

        runner = CliRunner()
        transport = self._make_mock_transport()

        terminal_result = {
            "terminal": "xterm",
            "args": ["-fn", "fixed"],
            "startup_ms": 350,
            "display": ":1",
        }

        with (
            patch("sys.platform", "linux"),
            patch("platform.system", return_value="Linux"),
            patch.dict("os.environ", {"DISPLAY": ":1"}),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=transport,
            ),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=terminal_result,
            ),
        ):
            result = runner.invoke(
                latency_cmd, ["--shots", "1", "--terminal", "--json"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "terminal_startup" in data
        assert data["terminal_startup"]["startup_ms"] == 350

    def test_terminal_error_displayed_in_text_mode(self):
        """When terminal measurement fails, error is displayed in text output."""
        from gptme.cli.cmd_computer import latency_cmd

        runner = CliRunner()
        transport = self._make_mock_transport()

        terminal_result = {"error": "no terminal emulator found — install xterm"}

        with (
            patch("sys.platform", "linux"),
            patch("platform.system", return_value="Linux"),
            patch.dict("os.environ", {"DISPLAY": ":1"}),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=transport,
            ),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=terminal_result,
            ),
        ):
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0
        assert "no terminal emulator found" in result.output

    @pytest.mark.parametrize(
        ("startup_ms", "expected_verdict"),
        [
            (100, "fast"),
            (499, "fast"),
            (500, "slow"),
            (1999, "slow"),
            (2000, "very slow"),
        ],
    )
    def test_terminal_startup_verdict_thresholds(self, startup_ms, expected_verdict):
        """Startup ms buckets map to the right human-readable verdict."""
        from gptme.cli.cmd_computer import latency_cmd

        runner = CliRunner()
        transport = self._make_mock_transport()

        terminal_result = {
            "terminal": "xterm",
            "args": ["-fn", "fixed"],
            "startup_ms": startup_ms,
            "display": ":1",
        }

        with (
            patch("sys.platform", "linux"),
            patch("platform.system", return_value="Linux"),
            patch.dict("os.environ", {"DISPLAY": ":1"}),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=transport,
            ),
            patch(
                "gptme.cli.cmd_computer._measure_terminal_startup",
                return_value=terminal_result,
            ),
        ):
            result = runner.invoke(latency_cmd, ["--shots", "1", "--terminal"])

        assert result.exit_code == 0, result.output
        assert expected_verdict in result.output
