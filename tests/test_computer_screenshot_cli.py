"""Tests for gptme-util computer screenshot (cmd_computer.py).

Unit-tests the screenshot CLI command without requiring a real X display or
scrot binary.  All subprocess calls are monkey-patched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from gptme.cli.cmd_computer import screenshot_cmd


class TestScreenshotCmd:
    """Tests for `gptme-util computer screenshot`."""

    def test_help_text_mentions_output(self):
        runner = CliRunner()
        result = runner.invoke(screenshot_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--display" in result.output
        assert "Linux only" in result.output

    def test_linux_screenshot_saved_to_default_path(self, tmp_path):
        """On Linux, a successful scrot run writes the screenshot and prints the path."""
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            # Simulate scrot writing the file
            Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("shutil.which", return_value="/usr/bin/scrot"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(screenshot_cmd, ["--output", str(out)])

        assert result.exit_code == 0, result.output
        assert str(out) in result.output
        assert "bytes" in result.output

    def test_linux_missing_scrot_exits_with_error(self):
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("shutil.which", return_value=None),
        ):
            result = runner.invoke(screenshot_cmd, [])

        assert result.exit_code != 0
        assert "scrot" in result.output

    def test_linux_display_error_gives_actionable_hint(self, tmp_path):
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            raise subprocess.CalledProcessError(
                1,
                cmd,
                b"",
                b"scrot: Can't open X display. It *is* running, yeah? [:99]",
            )

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("shutil.which", return_value="/usr/bin/scrot"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(
                screenshot_cmd, ["--output", str(out), "--display", ":99"]
            )

        assert result.exit_code != 0
        assert "Xvfb" in result.output
        assert "Xvfb :99 -screen 0 1024x768x24 &" in result.output
        assert "export DISPLAY=:99" in result.output

    def test_linux_scrot_timeout_exits_with_error(self, tmp_path):
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("shutil.which", return_value="/usr/bin/scrot"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(screenshot_cmd, ["--output", str(out)])

        assert result.exit_code != 0
        assert "timed out" in result.output

    def test_macos_screenshot_saved(self, tmp_path):
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(screenshot_cmd, ["--output", str(out)])

        assert result.exit_code == 0, result.output
        assert str(out) in result.output

    def test_macos_screencapture_missing(self, tmp_path):
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(screenshot_cmd, ["--output", str(out)])

        assert result.exit_code != 0
        assert "screencapture" in result.output

    def test_macos_screencapture_timeout_exits_with_error(self, tmp_path):
        out = tmp_path / "screen.png"

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Darwin"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(screenshot_cmd, ["--output", str(out)])

        assert result.exit_code != 0
        assert "timed out" in result.output

    def test_custom_display_passed_to_env(self, tmp_path):
        """--display value should be set in the subprocess env."""
        out = tmp_path / "screen.png"
        captured_env: dict = {}

        def fake_run(cmd, **kwargs):
            captured_env.update(kwargs.get("env", {}))
            Path(cmd[-1]).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("shutil.which", return_value="/usr/bin/scrot"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = runner.invoke(
                screenshot_cmd, ["--output", str(out), "--display", ":42"]
            )

        assert result.exit_code == 0, result.output
        assert captured_env.get("DISPLAY") == ":42"
