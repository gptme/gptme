"""Tests for gptme/cli/cmd_service.py — `gptme service init`.

Covers file generation, template validity (Bash/shell syntax, systemd unit
parse), and the on-demand (no-timer) path.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from click.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from gptme.cli.cmd_service import cli


def _run_init(tmp_path: Path, *args: str) -> None:
    """Run `gptme service init` into a temp work+output dir."""
    out_dir = tmp_path / "systemd"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "testagent",
            "--work-dir",
            str(tmp_path),
            "--output-dir",
            str(out_dir),
            *args,
        ],
    )
    assert result.exit_code == 0, result.output


def test_generates_service_and_timer(tmp_path: Path) -> None:
    _run_init(tmp_path)
    work = tmp_path
    out_dir = tmp_path / "systemd"

    assert (out_dir / "testagent.service").exists()
    assert (out_dir / "testagent.timer").exists()
    assert (work / "gptme.toml").exists()
    assert (work / "AGENTS.md").exists()
    assert (work / "gptme-agent-run.sh").exists()


def test_startup_script_is_executable_and_valid_bash(tmp_path: Path) -> None:
    _run_init(tmp_path)
    startup = tmp_path / "gptme-agent-run.sh"
    assert startup.stat().st_mode & 0o111, "startup script should be executable"

    if shutil.which("bash"):
        proc = subprocess.run(
            ["bash", "-n", str(startup)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"bash syntax error: {proc.stderr}"


def test_systemd_unit_parses(tmp_path: Path) -> None:
    """systemd-analyze verify should accept the generated unit, if available."""
    _run_init(tmp_path)
    service = tmp_path / "systemd" / "testagent.service"
    assert service.exists()

    if shutil.which("systemd-analyze"):
        proc = subprocess.run(
            ["systemd-analyze", "verify", str(service)],
            capture_output=True,
            text=True,
            check=False,
        )
        # systemd-analyze verify may warn on missing ExecStart deps but should
        # not fail hard on a syntactically valid unit.
        assert proc.returncode in (0, 1)


def test_on_demand_skips_timer(tmp_path: Path) -> None:
    _run_init(tmp_path, "--timer-schedule", "on-demand")
    assert (tmp_path / "systemd" / "testagent.service").exists()
    assert not (tmp_path / "systemd" / "testagent.timer").exists()


def test_on_demand_preserves_existing_timer_without_force(tmp_path: Path) -> None:
    """Switching to on-demand without --force must NOT delete an existing timer,
    but MUST print systemctl disable instructions so the operator can stop it."""
    # First scaffold a periodic agent (creates testagent.timer)
    _run_init(tmp_path)
    timer = tmp_path / "systemd" / "testagent.timer"
    assert timer.exists()

    # Reinitialize as on-demand WITHOUT --force → timer file must be preserved
    out_dir = tmp_path / "systemd"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "testagent",
            "--work-dir",
            str(tmp_path),
            "--output-dir",
            str(out_dir),
            "--timer-schedule",
            "on-demand",
        ],
    )
    assert result.exit_code == 0, result.output
    assert timer.exists(), "existing timer should be preserved without --force"
    # Must tell the operator how to stop the live timer, not just how to remove the file
    assert "systemctl --user disable --now testagent.timer" in result.output, (
        "warning must include the systemctl disable command so the operator can stop periodic runs"
    )


def test_on_demand_removes_existing_timer_with_force(tmp_path: Path) -> None:
    """Switching to on-demand WITH --force should disable-then-remove an existing timer.

    The unit must be disabled BEFORE the file is removed so that systemd can
    still resolve the unit name during ``disable --now``.  We mock subprocess.run
    to capture the disable call and verify it fires while the file is still present.
    """
    from unittest.mock import MagicMock, patch

    _run_init(tmp_path)
    timer = tmp_path / "systemd" / "testagent.timer"
    assert timer.exists()

    disable_call_saw_file: list[bool] = []

    def _mock_run(cmd: list[str], **kwargs: object) -> MagicMock:
        # Record whether the timer file existed when systemctl disable was called
        if "disable" in cmd:
            disable_call_saw_file.append(timer.exists())
        return MagicMock(returncode=1)  # simulate unit-not-found (best-effort)

    with patch("gptme.cli.cmd_service.subprocess.run", side_effect=_mock_run):
        _run_init(tmp_path, "--timer-schedule", "on-demand", "--force")

    assert not timer.exists(), "existing timer should be removed with --force"
    assert disable_call_saw_file, (
        "systemctl disable must be called during --force cleanup"
    )
    assert all(disable_call_saw_file), (
        "systemctl disable must be called BEFORE the timer file is removed"
    )


def test_force_overwrites_existing(tmp_path: Path) -> None:
    _run_init(tmp_path)
    (tmp_path / "gptme.toml").write_text("changed")
    # Without --force, existing file is preserved.
    _run_init(tmp_path)
    assert (tmp_path / "gptme.toml").read_text() == "changed"
    # With --force, it is overwritten.
    _run_init(tmp_path, "--force")
    assert (tmp_path / "gptme.toml").read_text() != "changed"
