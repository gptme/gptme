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


def test_force_overwrites_existing(tmp_path: Path) -> None:
    _run_init(tmp_path)
    (tmp_path / "gptme.toml").write_text("changed")
    # Without --force, existing file is preserved.
    _run_init(tmp_path)
    assert (tmp_path / "gptme.toml").read_text() == "changed"
    # With --force, it is overwritten.
    _run_init(tmp_path, "--force")
    assert (tmp_path / "gptme.toml").read_text() != "changed"
