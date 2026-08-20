"""Tests for gptme/cli/cmd_service.py — `gptme service init`.

Covers file generation, template validity (Bash/shell syntax, systemd unit
parse), and the on-demand (no-timer) path.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest
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
        # The generated unit (with its ExecStart script actually present in
        # tmp_path) should verify clean — a nonzero return means a real error.
        assert proc.returncode == 0, (
            f"systemd-analyze verify failed:\n{proc.stdout}\n{proc.stderr}"
        )


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


def test_on_demand_force_removes_timer_when_disable_succeeds(tmp_path: Path) -> None:
    """Switching to on-demand WITH --force should disable-then-remove an existing timer.

    The unit must be disabled BEFORE the file is removed so that systemd can
    still resolve the unit name during ``disable --now``.  We mock subprocess.run
    to capture the disable call (returncode=0) and verify it fires while the file
    is still present, after which the file is removed.
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
        return MagicMock(returncode=0)  # simulate successful disable

    out_dir = tmp_path / "systemd"
    runner = CliRunner()
    with patch("gptme.cli.cmd_service.subprocess.run", side_effect=_mock_run):
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
                "--force",
            ],
        )
    assert result.exit_code == 0, result.output

    assert not timer.exists(), (
        "existing timer should be removed with --force when disable succeeds"
    )
    assert disable_call_saw_file, (
        "systemctl disable must be called during --force cleanup"
    )
    assert all(disable_call_saw_file), (
        "systemctl disable must be called BEFORE the timer file is removed"
    )


def test_on_demand_force_preserves_timer_when_disable_fails(tmp_path: Path) -> None:
    """When disable --now returns nonzero, the timer file must be preserved.

    Removing the file after a failed disable makes the loaded unit unresolvable
    and harder to stop later.  A warning must be emitted so the operator knows
    periodic runs may continue.
    """
    from unittest.mock import MagicMock, patch

    _run_init(tmp_path)
    timer = tmp_path / "systemd" / "testagent.timer"
    assert timer.exists()

    out_dir = tmp_path / "systemd"
    runner = CliRunner()
    with patch(
        "gptme.cli.cmd_service.subprocess.run",
        return_value=MagicMock(returncode=1),
    ):
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
                "--force",
            ],
        )
    assert result.exit_code == 0, result.output

    assert timer.exists(), "timer file must be preserved when disable fails"
    assert "Warning" in result.output, (
        "a warning should be printed when systemctl disable fails"
    )


@pytest.mark.parametrize(
    "error", [FileNotFoundError("systemctl not found"), PermissionError("denied")]
)
def test_on_demand_force_handles_systemctl_os_error(
    tmp_path: Path, error: OSError
) -> None:
    """OS errors invoking systemctl must warn and preserve the timer file."""
    from unittest.mock import patch

    _run_init(tmp_path)
    timer = tmp_path / "systemd" / "testagent.timer"
    assert timer.exists()

    out_dir = tmp_path / "systemd"
    runner = CliRunner()
    with patch(
        "gptme.cli.cmd_service.subprocess.run",
        side_effect=error,
    ):
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
                "--force",
            ],
        )
    assert result.exit_code == 0, result.output
    assert timer.exists(), "timer file must be preserved when systemctl cannot run"
    assert "Warning" in result.output, "must warn when systemctl cannot run"
    # Scaffold must complete (workspace files still generated)
    assert (tmp_path / "gptme.toml").exists(), (
        "scaffolding must continue despite systemctl failure"
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


def test_existing_startup_script_is_made_executable(tmp_path: Path) -> None:
    """Reinitializing repairs startup permissions without overwriting its contents."""
    _run_init(tmp_path)
    startup = tmp_path / "gptme-agent-run.sh"
    startup.write_text("#!/bin/bash\necho custom\n")
    startup.chmod(0o644)

    _run_init(tmp_path)

    assert startup.read_text() == "#!/bin/bash\necho custom\n"
    assert startup.stat().st_mode & 0o111


def test_startup_script_uses_portable_date(tmp_path: Path) -> None:
    """The generated script must not depend on GNU-only date options."""
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()
    assert "date -u +%Y-%m-%dT%H:%M:%SZ" in startup
    assert "date --iso-8601" not in startup


def test_invalid_name_rejected(tmp_path: Path) -> None:
    """Names with shell metacharacters or spaces must be rejected at the CLI level."""
    from click.testing import CliRunner

    runner = CliRunner()
    for bad_name in ['x"; touch /tmp/pwned; #', "my agent", "a/b", "foo@bar"]:
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                bad_name,
                "--work-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "systemd"),
            ],
        )
        assert result.exit_code != 0, (
            f"should reject invalid name {bad_name!r}, got exit 0"
        )
        assert "invalid characters" in result.output.lower(), (
            f"should report invalid characters for {bad_name!r}"
        )


def test_service_unit_has_no_user_directive(tmp_path: Path) -> None:
    """The generated service unit must not contain 'User=' (invalid in user units)."""
    _run_init(tmp_path)
    service_text = (tmp_path / "systemd" / "testagent.service").read_text()
    assert "User=" not in service_text, (
        "User= directive is not supported in systemd user units and must not be generated"
    )


def test_work_dir_percent_is_escaped_in_unit(tmp_path: Path) -> None:
    """A work-dir containing '%' must not be expanded as a systemd specifier."""
    work = tmp_path / "100% gptme-agent"
    work.mkdir()
    out_dir = tmp_path / "systemd2"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "pctagent",
            "--work-dir",
            str(work),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    service_text = (out_dir / "pctagent.service").read_text()
    assert "%%" in service_text, "literal '%' must be doubled to '%%' for systemd"
    assert f"WorkingDirectory={work}".replace("%", "%%") in service_text


def test_work_dir_dollar_is_escaped_in_execstart(tmp_path: Path) -> None:
    """A work-dir containing '$' must stay literal in an ExecStart command."""
    work = tmp_path / "gptme-$agent"
    work.mkdir()
    out_dir = tmp_path / "systemd-dollar"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "dollaragent",
            "--work-dir",
            str(work),
            "--output-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    service_text = (out_dir / "dollaragent.service").read_text()
    escaped_work = str(work).replace("$", r"\x24")
    assert f"WorkingDirectory={work}" in service_text
    assert f'ExecStart=:"{escaped_work}/gptme-agent-run.sh"' in service_text


@pytest.mark.parametrize(
    "value",
    [
        "evil\nExecStartPre=/bin/true",
        'evil"dir',
        "evil'dir",
        "evil\\dir",
    ],
)
def test_unsafe_work_dir_rejected(tmp_path: Path, value: str) -> None:
    """Unsafe unit-file and executable-path characters must be rejected."""
    from unittest.mock import patch

    runner = CliRunner()
    with patch(
        "gptme.cli.cmd_service._resolve_work_dir",
        return_value=tmp_path / value,
    ):
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                "unsafeagent",
                "--work-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "systemd3"),
            ],
        )
    assert result.exit_code != 0, f"unsafe work-dir {value!r} must be rejected"


def test_model_with_spaces_is_quoted_in_unit(tmp_path: Path) -> None:
    """A model containing spaces must remain one Environment assignment."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "modelagent",
            "--model",
            "gpt-4o mini",
            "--work-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "systemd-model-space"),
        ],
    )
    assert result.exit_code == 0, result.output
    service_text = (tmp_path / "systemd-model-space" / "modelagent.service").read_text()
    assert 'Environment="GPTME_AGENT_MODEL=gpt-4o mini"' in service_text


def test_model_newline_rejected(tmp_path: Path) -> None:
    """A model must not inject an additional systemd directive."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "modelagent",
            "--model",
            "gpt-4o-mini\nEnvironment=GPTME_MALICIOUS=1",
            "--work-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "systemd4"),
        ],
    )
    assert result.exit_code != 0, "newline in model must be rejected"
    assert not (tmp_path / "systemd4" / "modelagent.service").exists()
