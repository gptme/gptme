"""Tests for `gptme-util computer doctor` (cmd_computer.py).

Unit-tests the doctor CLI command without requiring a real display, X11 tools,
or Playwright browser binary.  All external dependencies are monkey-patched.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gptme.cli.cmd_computer import doctor_cmd


def _fake_transport(tmp_path):
    """Return a mock transport that writes a stub PNG on every screenshot call."""
    transport = MagicMock()

    def _fake_shot(**_kw):
        p = tmp_path / "shot.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        return p

    transport.screenshot.side_effect = _fake_shot
    return transport


def _display_asserting_transport(tmp_path, expected_display: str):
    """Return a mock transport that verifies DISPLAY is set during screenshots."""
    transport = MagicMock()

    def _fake_shot(**_kw):
        assert expected_display == os.environ.get("DISPLAY")
        p = tmp_path / "shot.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        return p

    transport.screenshot.side_effect = _fake_shot
    return transport


class TestDoctorHelp:
    def test_help_text(self):
        runner = CliRunner()
        result = runner.invoke(doctor_cmd, ["--help"])
        assert result.exit_code == 0
        assert "--display" in result.output


class TestDoctorOutputFormat:
    """Doctor output format invariants that hold regardless of platform state."""

    def test_output_contains_platform_line(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", return_value=None),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "Platform:" in result.output
        assert "Linux" in result.output

    def test_output_contains_browser_section(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", return_value=None),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "Browser" in result.output or "playwright" in result.output.lower()

    def test_summary_line_always_present(self, monkeypatch, tmp_path):
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", return_value=None),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "passed" in result.output or "failed" in result.output


class TestDoctorLinux:
    """Doctor behaviour on Linux."""

    def _base_patches(self, tmp_path, which_fn=None, transport=None):
        """Return a list of context managers for a basic Linux-healthy setup."""

        def _default_which(cmd):
            return f"/usr/bin/{cmd}"

        if which_fn is None:
            which_fn = _default_which
        if transport is None:
            transport = _fake_transport(tmp_path)
        return [
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", side_effect=which_fn),
            patch(
                "gptme.tools.computer_transport.get_transport", return_value=transport
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ]

    def test_exit_0_on_healthy_linux(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()

        # Use /usr/bin/env as the "chromium" path — it always exists on Linux
        _chromium_path = "/usr/bin/env"
        pw_stub = MagicMock()
        pw_cm = MagicMock()
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path=_chromium_path)
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
            patch.dict(
                sys.modules, {"playwright.sync_api": pw_stub, "pyatspi": MagicMock()}
            ),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output or "✅" in result.output

    def test_reports_display_missing(self, monkeypatch, tmp_path):
        """When $DISPLAY is not set, doctor reports it."""
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        # Display check section should report the missing display
        assert "$DISPLAY" in result.output or "not set" in result.output

    def test_reports_xdotool_missing(self, monkeypatch, tmp_path):
        """When xdotool is absent, the doctor reports it."""
        monkeypatch.setenv("DISPLAY", ":1")

        def fake_which(cmd):
            if cmd == "xdotool":
                return None
            return f"/usr/bin/{cmd}"

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", side_effect=fake_which),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "xdotool" in result.output

    def test_display_flag_appears_in_output(self, monkeypatch, tmp_path):
        """When --display is passed, its value appears in the output."""
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, ["--display", ":99"])
        assert ":99" in result.output

    def test_display_flag_drives_latency_transport(self, monkeypatch, tmp_path):
        """The latency sample honors --display even when $DISPLAY is unset."""
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        transport = _display_asserting_transport(tmp_path, ":99")

        # Mock playwright to avoid ImportError when it's not installed
        pw_stub = MagicMock()
        pw_cm = MagicMock()
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path="/usr/bin/env")
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch(
                "gptme.tools.computer_transport.NativeComputerTransport",
                return_value=transport,
            ),
            patch.dict(
                sys.modules, {"playwright.sync_api": pw_stub, "pyatspi": MagicMock()}
            ),
        ):
            result = runner.invoke(doctor_cmd, ["--display", ":99"])

        assert result.exit_code == 0, result.output
        assert "no display available" not in result.output
        assert "median=" in result.output or "ms" in result.output

    def test_optional_checks_render_as_warnings(self, monkeypatch):
        """Advisory checks should show warning output instead of green success."""
        monkeypatch.setenv("DISPLAY", ":1")

        def fake_which(cmd):
            if cmd == "scrot":
                return None
            return f"/usr/bin/{cmd}"

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", side_effect=fake_which),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert "!  scrot not found, ffmpeg available (fallback)" in result.output
        assert (
            "!  pyatspi not installed (accessibility_tree action disabled)"
            in result.output
        )
        assert "pip install pyatspi" in result.output

    def test_failures_exit_nonzero(self, monkeypatch):
        """Failing checks should produce a non-zero exit code."""
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", return_value=None),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert result.exit_code == 1
        assert "check(s) failed" in result.output


class TestDoctorMacOS:
    """Doctor behaviour on macOS."""

    def test_exit_0_on_healthy_macos(self, tmp_path):
        runner = CliRunner()
        # Use /usr/bin/env as the "chromium" path — it always exists
        _chromium_path = "/usr/bin/env"
        pw_stub = MagicMock()
        pw_cm = MagicMock()
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path=_chromium_path)
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        with (
            patch("platform.system", return_value="Darwin"),
            patch("platform.machine", return_value="arm64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
            patch.dict(sys.modules, {"playwright.sync_api": pw_stub}),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert result.exit_code == 0, result.output
        assert "All checks passed" in result.output or "✅" in result.output

    def test_reports_cliclick_missing(self):
        """When cliclick is absent, the doctor mentions it."""
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Darwin"),
            patch("platform.machine", return_value="arm64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: (
                    None if cmd == "cliclick" else f"/usr/bin/{cmd}"
                ),
            ),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "cliclick" in result.output

    def test_missing_osascript_exits_nonzero(self, tmp_path):
        """Missing osascript is a failed macOS doctor check."""
        runner = CliRunner()
        pw_stub = MagicMock()
        pw_cm = MagicMock()
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path="/usr/bin/env")
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        def fake_which(cmd):
            if cmd == "osascript":
                return None
            return f"/usr/bin/{cmd}"

        with (
            patch("platform.system", return_value="Darwin"),
            patch("platform.machine", return_value="arm64"),
            patch("gptme.cli.cmd_computer.shutil.which", side_effect=fake_which),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
            patch.dict(sys.modules, {"playwright.sync_api": pw_stub}),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert result.exit_code == 1
        assert "osascript missing" in result.output
        assert "check(s) failed" in result.output

    def test_no_display_section_on_macos(self):
        """macOS doctor output should not show X11/DISPLAY section."""
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Darwin"),
            patch("platform.machine", return_value="arm64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        # $DISPLAY check is Linux-only
        assert "$DISPLAY" not in result.output


class TestDoctorPlaywright:
    """Doctor reports Playwright status correctly."""

    def test_reports_playwright_not_installed(self, monkeypatch, tmp_path):
        """When playwright cannot be imported, doctor warns but exits 0.

        Native desktop control (xdotool/scrot) works without playwright.
        Playwright is only needed for the browser tool, so its absence is a
        warning (like pyatspi), not a hard failure.
        """
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()
        transport_mock = _fake_transport(tmp_path)
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=transport_mock,
            ),
            patch(
                "gptme.tools.computer_transport.NativeComputerTransport",
                return_value=transport_mock,
            ),
            patch.dict(
                sys.modules,
                {
                    "playwright": None,
                    "playwright.sync_api": None,
                    "pyatspi": MagicMock(),
                },
            ),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "playwright" in result.output.lower()
        # playwright missing is a warning, not a hard failure — native computer use still works
        assert result.exit_code == 0, (
            f"Expected exit 0 (warning), got {result.exit_code}:\n{result.output}"
        )

    def test_reports_chromium_binary_missing(self, monkeypatch, tmp_path):
        """When playwright is installed but chromium binary is missing, doctor reports it."""
        monkeypatch.setenv("DISPLAY", ":1")

        pw_stub = MagicMock()
        pw_cm = MagicMock()
        # chromium executable path returns a non-existent path
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path="/nonexistent/chromium")
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
            patch.dict(sys.modules, {"playwright.sync_api": pw_stub}),
        ):
            result = runner.invoke(doctor_cmd, [])
        # chromium check must mention playwright or chromium
        assert (
            "chromium" in result.output.lower() or "playwright" in result.output.lower()
        )


class TestDoctorLatencySection:
    """Doctor's latency sample section."""

    def test_latency_sample_shown_when_transport_available(self, monkeypatch, tmp_path):
        """When a transport is available, a latency sample line is emitted."""
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                return_value=_fake_transport(tmp_path),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        assert "median=" in result.output or "ms" in result.output

    def test_latency_section_skipped_when_no_transport(self, monkeypatch):
        """When no display is available, the latency section reports the error."""
        monkeypatch.delenv("DISPLAY", raising=False)
        runner = CliRunner()
        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch("gptme.cli.cmd_computer.shutil.which", return_value=None),
            patch("gptme.tools.computer_transport.get_transport", return_value=None),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
        ):
            result = runner.invoke(doctor_cmd, [])
        # Either "no display" or "display" must appear in the output
        assert "display" in result.output.lower() or "DISPLAY" in result.output

    def test_latency_exception_exits_nonzero(self, monkeypatch):
        """Unexpected latency-section failures are failed doctor checks."""
        monkeypatch.setenv("DISPLAY", ":1")
        runner = CliRunner()
        pw_stub = MagicMock()
        pw_cm = MagicMock()
        pw_cm.__enter__ = lambda s: MagicMock(
            chromium=MagicMock(executable_path="/usr/bin/env")
        )
        pw_cm.__exit__ = lambda *a: None
        pw_stub.sync_playwright.return_value = pw_cm

        with (
            patch("platform.system", return_value="Linux"),
            patch("platform.machine", return_value="x86_64"),
            patch(
                "gptme.cli.cmd_computer.shutil.which",
                side_effect=lambda cmd: f"/usr/bin/{cmd}",
            ),
            patch(
                "gptme.tools.computer_transport.get_transport",
                side_effect=RuntimeError("transport boom"),
            ),
            patch("gptme.tools.computer_transport.NativeComputerTransport", MagicMock),
            patch.dict(
                sys.modules, {"playwright.sync_api": pw_stub, "pyatspi": MagicMock()}
            ),
        ):
            result = runner.invoke(doctor_cmd, [])

        assert result.exit_code == 1
        assert "could not measure latency: transport boom" in result.output
        assert "check(s) failed" in result.output
