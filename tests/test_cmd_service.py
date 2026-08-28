"""Tests for gptme/cli/cmd_service.py — `gptme service init`.

Covers file generation, template validity (Bash/shell syntax, systemd unit
parse), and the on-demand (no-timer) path.
"""

from __future__ import annotations

import json
import os
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


def test_health_check_is_opt_in(tmp_path: Path) -> None:
    _run_init(tmp_path)
    assert not (tmp_path / "health-check.py").exists()
    assert not (tmp_path / "HEALTH.md").exists()


def test_health_check_generates_executable_local_probe(tmp_path: Path) -> None:
    _run_init(tmp_path, "--platform", "linux", "--enable-health-check")

    health_check = tmp_path / "health-check.py"
    health_docs = tmp_path / "HEALTH.md"
    assert health_check.stat().st_mode & 0o111
    assert health_docs.exists()

    script = health_check.read_text()
    docs = health_docs.read_text()
    assert '"testagent.service"' in script
    assert '"--property=ActiveState"' in script
    assert 'properties.get("ActiveState"' in script
    assert "datetime.now(timezone.utc)" in script
    assert "does **not** start an HTTP server" in docs
    assert "curl" not in docs


def test_health_check_reports_state_and_preserves_failure_exit_code(
    tmp_path: Path,
) -> None:
    _run_init(tmp_path, "--platform", "linux", "--enable-health-check")
    health_check = tmp_path / "health-check.py"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\nprintf 'LoadState=loaded\\nActiveState=inactive\\n'\nexit 0\n"
    )
    systemctl.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}

    proc = subprocess.run(
        [str(health_check)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 1
    assert json.loads(proc.stdout) | {
        "load_state": "loaded",
        "active_state": "inactive",
        "status": "unhealthy",
    } == json.loads(proc.stdout)


def test_health_check_error_uses_stable_json_schema(tmp_path: Path) -> None:
    _run_init(tmp_path, "--platform", "linux", "--enable-health-check")
    health_check = tmp_path / "health-check.py"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    env = {**os.environ, "PATH": str(fake_bin)}

    proc = subprocess.run(
        ["/usr/bin/python3", str(health_check)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["load_state"] == "unknown"
    assert payload["active_state"] == "unknown"
    assert payload["status"] == "error"
    assert "error" in payload


def test_health_check_does_not_chmod_skipped_file(tmp_path: Path) -> None:
    health_check = tmp_path / "health-check.py"
    health_check.write_text("user-owned\n")
    health_check.chmod(0o600)

    _run_init(tmp_path, "--platform", "linux", "--enable-health-check")

    assert health_check.read_text() == "user-owned\n"
    assert health_check.stat().st_mode & 0o777 == 0o600


def test_health_check_rejects_macos(tmp_path: Path) -> None:
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
            str(tmp_path / "launchd"),
            "--platform",
            "macos",
            "--enable-health-check",
        ],
    )

    assert result.exit_code == 2
    assert "supports Linux systemd services only" in result.output
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_agents_md_references_template_and_multiple_service_managers(
    tmp_path: Path,
) -> None:
    """Generated agent instructions should not imply systemd is the only runtime."""
    _run_init(tmp_path)
    agents_md = (tmp_path / "AGENTS.md").read_text()

    assert "Linux systemd user unit or a macOS launchd agent" in agents_md
    assert "gptme-agent-template" in agents_md
    assert "batteries-included workspace" in agents_md
    assert "It runs on a systemd timer" not in agents_md


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


def test_model_newline_rejected_on_macos(tmp_path: Path) -> None:
    """A model with a newline must be rejected on macOS just as on Linux."""
    out_dir = tmp_path / "launchd"
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
            str(out_dir),
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code != 0, "newline in model must be rejected on macOS too"
    assert not (out_dir / "com.gptme.modelagent.plist").exists()


def test_detect_platform_unsupported_raises(tmp_path: Path) -> None:
    """Running with --platform auto on an unsupported OS must raise a usage error."""
    from unittest.mock import patch

    runner = CliRunner()
    with patch("gptme.cli.cmd_service.platform.system", return_value="Windows"):
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                "agent",
                "--work-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
                "--platform",
                "auto",
            ],
        )
    assert result.exit_code != 0, "unsupported platform must produce a non-zero exit"
    assert "not supported" in (result.output + str(result.exception)).lower()


def test_macos_autodetect_emits_warning(tmp_path: Path) -> None:
    """Auto-detecting macOS should print a note about launchd vs systemd.

    The note is only emitted when --output-dir is NOT passed (output_dir is None),
    so we must NOT pass --output-dir here or the note path is never reached.
    """
    from unittest.mock import patch

    runner = CliRunner()
    with patch("gptme.cli.cmd_service.platform.system", return_value="Darwin"):
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                "agent",
                "--work-dir",
                str(tmp_path),
                # no --output-dir: note fires only when output_dir is None
                "--platform",
                "auto",
            ],
            env={"HOME": str(tmp_path)},  # redirect ~/Library/LaunchAgents to tmp
        )
    assert result.exit_code == 0, f"should succeed; got: {result.output}"
    # CliRunner mixes stderr into .output by default.
    # The note specifically mentions "launchd plist" — check for that phrase
    # to ensure the actual warning path was exercised (not just the scaffold line).
    assert "launchd plist" in result.output.lower(), (
        "expected the launchd-plist note in output (fires only when output_dir is None)"
    )


def test_explicit_macos_platform_does_not_claim_autodetection(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "agent",
            "--work-dir",
            str(tmp_path),
            "--platform",
            "macos",
        ],
        env={"HOME": str(tmp_path)},
    )

    assert result.exit_code == 0, result.output
    assert "macOS detected" not in result.output


def test_help_references_launchd_and_full_template() -> None:
    """CLI help should name the minimal/full-template boundary."""
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--help"])

    assert result.exit_code == 0, result.output
    assert "systemd on" in result.output
    assert "Linux, launchd on macOS" in result.output
    assert "gptme/gptme-" in result.output
    assert "agent-template" in result.output


def test_launchd_plist_generated_on_macos_platform(tmp_path: Path) -> None:
    """When --platform=macos, a launchd plist should be generated instead of systemd files."""
    out_dir = tmp_path / "launchd"
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
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code == 0, result.output
    work = tmp_path

    # launchd plist should be generated
    plist_file = out_dir / "com.gptme.testagent.plist"
    assert plist_file.exists(), "should generate com.gptme.{name}.plist"
    assert "com.gptme.testagent" in result.output

    # NO systemd files
    assert not (out_dir / "testagent.service").exists()
    assert not (out_dir / "testagent.timer").exists()

    # Workspace files still generated
    assert (work / "gptme.toml").exists()
    assert (work / "AGENTS.md").exists()
    assert (work / "gptme-agent-run.sh").exists()

    agents_md = (work / "AGENTS.md").read_text()
    assert "macOS launchd agent" in agents_md
    assert "gptme-agent-template" in agents_md


def test_launchd_plist_is_valid_xml(tmp_path: Path) -> None:
    """The generated launchd plist should be valid XML that can be parsed."""
    import xml.etree.ElementTree as ET

    out_dir = tmp_path / "launchd"
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
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code == 0, result.output

    plist_file = out_dir / "com.gptme.testagent.plist"
    plist_text = plist_file.read_text()

    # Should parse as valid XML
    tree = ET.fromstring(plist_text)
    assert tree.tag == "plist"

    # Should have proper structure
    root_dict = tree.find("dict")
    assert root_dict is not None


def test_launchd_plist_contains_agent_variables(tmp_path: Path) -> None:
    """The generated launchd plist should contain proper agent env variables."""
    out_dir = tmp_path / "launchd"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "testagent",
            "--model",
            "gpt-4o-mini",
            "--work-dir",
            str(tmp_path),
            "--output-dir",
            str(out_dir),
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code == 0, result.output

    plist_file = out_dir / "com.gptme.testagent.plist"
    plist_text = plist_file.read_text()

    # Check key environment variables are present
    assert "GPTME_AGENT_NAME" in plist_text
    assert "testagent" in plist_text
    assert "GPTME_AGENT_MODEL" in plist_text
    assert "gpt-4o-mini" in plist_text
    # GPTME_NON_INTERACTIVE is deliberately absent: gptme never reads it (the
    # startup script passes --non-interactive as a flag), so shipping it in the
    # plist advertised a control that does nothing.
    assert "GPTME_NON_INTERACTIVE" not in plist_text


def test_launchd_plist_daily_schedule(tmp_path: Path) -> None:
    """launchd plist with daily schedule should include StartInterval."""
    out_dir = tmp_path / "launchd"
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
            "--platform",
            "macos",
            "--timer-schedule",
            "daily",
        ],
    )
    assert result.exit_code == 0, result.output

    plist_file = out_dir / "com.gptme.testagent.plist"
    plist_text = plist_file.read_text()

    # Daily schedule = 86400 seconds (1 day)
    assert "StartInterval" in plist_text
    assert "86400" in plist_text  # daily interval in seconds


def test_launchd_plist_on_demand_no_schedule(tmp_path: Path) -> None:
    """launchd plist with on-demand should have RunAtLoad=false and no StartInterval."""
    out_dir = tmp_path / "launchd"
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
            "--platform",
            "macos",
            "--timer-schedule",
            "on-demand",
        ],
    )
    assert result.exit_code == 0, result.output

    plist_file = out_dir / "com.gptme.testagent.plist"
    plist_text = plist_file.read_text()

    assert "<false/>" in plist_text  # RunAtLoad=false for on-demand (manual start only)
    assert "StartInterval" not in plist_text  # no periodic schedule


def test_launchd_logs_directory_created(tmp_path: Path) -> None:
    """launchd scaffolding should create a logs directory for plist output redirection."""
    out_dir = tmp_path / "launchd"
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
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code == 0, result.output

    logs_dir = tmp_path / "logs"
    assert logs_dir.exists() and logs_dir.is_dir(), "logs/ directory should be created"


def test_launchd_model_with_forbidden_chars_rejected(tmp_path: Path) -> None:
    """Model identifiers containing XML 1.0-forbidden chars must be rejected on macOS.

    Silently stripping them would cause the agent to run with a different model
    than the user requested. Validation is the correct response.
    """
    from unittest.mock import patch

    special_work = tmp_path / "work"
    special_work.mkdir()
    out_dir = tmp_path / "launchd"
    runner = CliRunner()
    # Inject control chars (\x01, \x0B) and a lone surrogate (\uD800) into the model
    with patch(
        "gptme.cli.cmd_service._resolve_work_dir",
        return_value=special_work,
    ):
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                "testagent",
                "--model",
                "gpt-4o\x01mini\x0b\ud800",
                "--work-dir",
                str(special_work),
                "--output-dir",
                str(out_dir),
                "--platform",
                "macos",
            ],
        )
    assert result.exit_code != 0, (
        "macOS scaffolding must reject model containing XML-forbidden control chars"
    )


def test_launchd_work_dir_with_forbidden_chars_rejected(tmp_path: Path) -> None:
    """Work-dir containing XML 1.0-forbidden chars must be rejected on macOS.

    Silently sanitizing the path in the plist would cause launchd to look for a
    nonexistent directory/script. Validation is the correct response.
    """
    from unittest.mock import patch

    special_work = tmp_path / "work\x01dir"
    special_work.mkdir()
    out_dir = tmp_path / "launchd"
    runner = CliRunner()
    with patch(
        "gptme.cli.cmd_service._resolve_work_dir",
        return_value=special_work,
    ):
        result = runner.invoke(
            cli,
            [
                "init",
                "--name",
                "testagent",
                "--work-dir",
                str(special_work),
                "--output-dir",
                str(out_dir),
                "--platform",
                "macos",
            ],
        )
    assert result.exit_code != 0, (
        "macOS scaffolding must reject work-dir containing XML-forbidden control chars"
    )


def test_macos_path_with_systemd_invalid_chars_accepted(tmp_path: Path) -> None:
    """Paths with apostrophes/backslashes are valid on macOS and must not be
    rejected by systemd-specific escaping when --platform macos is used."""
    # Create a subdir whose name contains a character systemd forbids but
    # launchd allows (apostrophe), then scaffold into it.
    special_work = tmp_path / "user's workspace"
    special_work.mkdir()
    out_dir = tmp_path / "launchd"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "init",
            "--name",
            "testagent",
            "--work-dir",
            str(special_work),
            "--output-dir",
            str(out_dir),
            "--platform",
            "macos",
        ],
    )
    assert result.exit_code == 0, (
        f"macOS scaffolding should accept apostrophes in work-dir; got: {result.output}"
    )


def test_startup_script_actually_invokes_gptme(tmp_path: Path) -> None:
    """The scaffolded service must run a gptme session, not just write a log stub.

    Regression: the original template wrote a session-log header and exited, so
    `systemctl start <name>.service` produced an empty journal file and never
    started an agent.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    assert 'gptme "${gptme_args[@]}"' in startup
    assert "--non-interactive" in startup
    assert '--workspace "$WORK_DIR"' in startup
    assert 'exit "$exit_code"' in startup


def test_startup_script_passes_non_interactive_as_a_flag(tmp_path: Path) -> None:
    """GPTME_NON_INTERACTIVE is not read by gptme; the flag must be explicit."""
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    # The env var alone must never be the only thing standing between a headless
    # unit and an interactive prompt.
    assert "gptme_args=(--non-interactive" in startup


def test_startup_script_forwards_configured_model(tmp_path: Path) -> None:
    """The model chosen at init time reaches gptme via GPTME_AGENT_MODEL."""
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    assert 'if [ -n "${GPTME_AGENT_MODEL:-}" ]; then' in startup
    assert 'gptme_args+=(--model "$GPTME_AGENT_MODEL")' in startup


def test_prompt_file_is_generated(tmp_path: Path) -> None:
    """A prompt.md scaffold ships so the first run has something to execute."""
    _run_init(tmp_path)
    prompt = tmp_path / "prompt.md"

    assert prompt.exists()
    assert prompt.read_text().strip()


def test_startup_script_fails_loudly_without_prompt(tmp_path: Path) -> None:
    """A missing prompt file must fail the unit, not silently produce nothing."""
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    assert 'if [ ! -f "$PROMPT_FILE" ]; then' in startup
    assert "exit 66" in startup
    assert "command -v gptme" in startup
    assert "exit 127" in startup


def test_startup_script_session_id_is_collision_resistant(tmp_path: Path) -> None:
    """Session IDs must be unique even when two runs start within the same second.

    Regression: the original template used date +%%Y%%m%%d-%%H%%M%%S alone, so
    two service restarts within a second would produce identical SESSION_ID
    values, causing the later run to truncate the earlier journal entry and
    reuse the same gptme conversation.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    # PID suffix ($$ in bash) + $RANDOM guards against PID reuse within the
    # same second on rapid restarts.
    assert "SESSION_ID=$(date +%Y%m%d-%H%M%S)-$$-$RANDOM" in startup


def test_startup_script_prompt_bypasses_command_routing(tmp_path: Path) -> None:
    """The prompt must not trigger gptme's subcommand dispatch.

    Regression: passing "$(cat prompt.md)" as the first positional argument lets
    gptme's dispatch logic (which checks prompts[0] for exact matches against
    gptme-util subcommands and gptme-* plugins) route a single-word prompt like
    'context', 'status', or 'tools' to a utility subcommand instead of starting
    an agent session. A leading newline prevents the exact-match while
    _group_prompt_args strips it before the LLM receives the message.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    # In the generated bash file, $'\n' is ANSI-C quoting for a newline; the
    # backslash is a literal \ in the file content, so Python sees it as \n.
    assert r"prompt_arg=$'\n'" in startup
    assert '"$prompt_arg"' in startup


def test_startup_script_checks_prompt_readability(tmp_path: Path) -> None:
    """A prompt file that exists but is unreadable must fail loudly (exit 66).

    Regression: the original template only checked [ ! -f "$PROMPT_FILE" ] for
    existence. An unreadable file passes that check, then `cat "$PROMPT_FILE"`
    fails silently (or with a confusing cat error), causing gptme to run with an
    empty prompt and exit 0 — appearing successful while doing nothing useful.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    assert '[ ! -r "$PROMPT_FILE" ]' in startup


def test_startup_script_checks_prompt_nonempty(tmp_path: Path) -> None:
    """An empty prompt file must fail loudly (exit 66), not run gptme silently.

    Regression: the original template only checked existence and readability.
    An empty prompt.md passes both checks, but `cat prompt.md` yields nothing,
    so gptme runs with an effective empty prompt, wastes an API call, and exits 0
    — appearing successful while doing nothing useful.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    assert '[ ! -s "$PROMPT_FILE" ]' in startup


def test_startup_script_rejects_slash_command_prompt(tmp_path: Path) -> None:
    """A prompt.md starting with a gptme in-chat command must be rejected (exit 66).

    Regression: the leading-newline guard ($'\\n') prevents CLI-level subcommand
    dispatch, but gptme's _group_prompt_args strips the newline before the chat
    loop dispatch. A first line like /shell or /python would be executed as an
    in-chat command rather than sent to the model. /path/to/file-style strings
    are safe because their first word has more than one slash.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    # The check must extract the first word and count its slashes.
    assert "_prompt_fw" in startup
    # Single-slash first words that match gptme's in-chat command pattern must
    # be rejected before gptme is invoked.
    assert "in-chat command" in startup


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
def test_startup_script_rejects_slash_command_with_leading_whitespace(
    tmp_path: Path,
) -> None:
    """A prompt.md whose first content line is TAB+/shell must be rejected (exit 66).

    Regression: the original guard used `${_prompt_fw%% *}` to extract the first
    word, which retains a leading tab — so `\\t/shell` passed the startswith-/
    check unchanged and was not caught. _group_prompt_args would then strip the
    tab and expose /shell to the in-chat dispatcher.
    Using `awk '{print $1}'` strips leading whitespace before extracting the word.
    """
    _run_init(tmp_path)
    startup = tmp_path / "gptme-agent-run.sh"
    (tmp_path / "prompt.md").write_text("\t/shell echo pwned\n")

    proc = subprocess.run(
        ["bash", str(startup)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 66, (
        f"Expected exit 66 for tab-prefixed slash command; got {proc.returncode}\n"
        f"stderr: {proc.stderr}"
    )
    assert "in-chat command" in proc.stderr


def test_startup_script_rejects_slash_command_with_unicode_whitespace(
    tmp_path: Path,
) -> None:
    """A prompt.md whose first content line starts with unicode whitespace then /shell
    must be rejected (exit 66).

    Regression: awk '{print $1}' only splits on ASCII whitespace. A non-breaking
    space (U+00A0) before /shell keeps it as the first awk token, bypassing the guard.
    Python's str.split() strips all Unicode whitespace, so \\u00a0/shell → /shell.
    """
    _run_init(tmp_path)
    startup = tmp_path / "gptme-agent-run.sh"
    # Write non-breaking space (U+00A0) followed by /shell
    (tmp_path / "prompt.md").write_bytes("\u00a0/shell echo pwned\n".encode())

    proc = subprocess.run(
        ["bash", str(startup)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 66, (
        f"Expected exit 66 for unicode-whitespace-prefixed slash command; got {proc.returncode}\n"
        f"stderr: {proc.stderr}"
    )
    assert "in-chat command" in proc.stderr


def test_startup_script_prompt_guard_fails_loud_without_python3(
    tmp_path: Path,
) -> None:
    """Prompt guard must fail loudly if python3 is absent from PATH.

    Regression: the guard used `| python3 -c ... || true`, so a missing python3
    set _prompt_fw to '' and silently let the slash-command check pass even for
    a prompt starting with /shell.
    """
    _run_init(tmp_path)
    startup = tmp_path / "gptme-agent-run.sh"
    (tmp_path / "prompt.md").write_text("/shell echo pwned\n")

    # Run with a PATH that has the system tools (bash, grep, awk, …) but no python3.
    # We create a fake python3 shim that exits 127 to simulate a missing interpreter.
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    fake_python3 = fake_bin / "python3"
    fake_python3.write_text("#!/bin/sh\nexit 127\n")
    fake_python3.chmod(0o755)
    # Prepend our fake_bin so it shadows any real python3, but keep system PATH
    # for bash, grep, mkdir, etc.
    new_path = f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"
    proc = subprocess.run(
        ["bash", str(startup)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": new_path},
    )
    # Must not exit 0 — failing open would allow /shell to reach the model dispatcher.
    assert proc.returncode != 0, (
        "Expected non-zero exit when python3 is absent; guard silently failed open"
    )


def test_startup_script_mkdir_journal_dir_fails_loudly(tmp_path: Path) -> None:
    """A journal directory that cannot be created must fail with an explicit error.

    Regression: the original template called `mkdir -p "$JOURNAL_DIR"` without
    checking its exit status. If the workspace lacks write permission, mkdir
    fails, the subsequent `> "$SESSION_LOG"` redirection also fails, and the
    script continues as if the journal exists, silently losing all session output.
    """
    _run_init(tmp_path)
    startup = (tmp_path / "gptme-agent-run.sh").read_text()

    # The mkdir line must exit on failure with a diagnostic message.
    assert 'mkdir -p "$JOURNAL_DIR"' in startup
    assert "cannot create journal dir" in startup


def test_service_unit_restart_backoff_is_actually_applied(tmp_path: Path) -> None:
    """RestartMaxDelaySec is inert without RestartSteps.

    Regression: the unit set `RestartSec=5` + `RestartMaxDelaySec=60` intending
    exponential backoff, but systemd ignores RestartMaxDelaySec unless
    RestartSteps is also set, logging "Service has RestartMaxDelaySec= but no
    RestartSteps= setting. Ignoring." and retrying at a flat 5s forever.
    Observed on a real `systemctl --user start` of a generated unit.
    """
    _run_init(tmp_path)
    unit = (tmp_path / "systemd" / "testagent.service").read_text()

    assert "RestartMaxDelaySec=" in unit
    assert "RestartSteps=" in unit, (
        "RestartMaxDelaySec is silently ignored by systemd without RestartSteps"
    )


def test_service_unit_gives_up_on_persistent_failure(tmp_path: Path) -> None:
    """A one-shot session that keeps failing must not retry forever.

    Regression: with `Restart=on-failure` and no start limit, a persistent
    failure (invalid API key, exhausted quota, unusable prompt) restarted the
    agent every 5 seconds indefinitely, hammering a paid API. Verified against a
    real run: an invalid key produced exit 76 and an immediate auto-restart.
    """
    _run_init(tmp_path)
    unit = (tmp_path / "systemd" / "testagent.service").read_text()

    assert "StartLimitIntervalSec=" in unit
    assert "StartLimitBurst=" in unit

    # The start limit must be declared in [Unit]; systemd ignores it in [Service].
    unit_section = unit.split("[Service]", 1)[0]
    assert "StartLimitIntervalSec=" in unit_section
    assert "StartLimitBurst=" in unit_section


def test_generated_units_do_not_set_dead_non_interactive_env(tmp_path: Path) -> None:
    """GPTME_NON_INTERACTIVE is never read by gptme; shipping it is misleading."""
    _run_init(tmp_path)
    unit = (tmp_path / "systemd" / "testagent.service").read_text()
    assert "GPTME_NON_INTERACTIVE" not in unit

    _run_init(tmp_path, "--platform", "macos")
    plist = (tmp_path / "systemd" / "com.gptme.testagent.plist").read_text()
    assert "GPTME_NON_INTERACTIVE" not in plist


def test_service_unit_has_runtime_max_sec(tmp_path: Path) -> None:
    """A hung LLM connection must not keep the unit active forever.

    Without RuntimeMaxSec the unit stays in active (running) state indefinitely
    when the gptme process hangs (endpoint unresponsive, infinite stream, etc.).
    Restart=on-failure never fires because the process has not exited, and timer
    triggers queue behind the still-active unit. RuntimeMaxSec kills the process
    after a deadline, moving the unit to failed and allowing restart + timer to
    proceed normally.
    """
    _run_init(tmp_path)
    unit = (tmp_path / "systemd" / "testagent.service").read_text()
    assert "RuntimeMaxSec=" in unit, (
        "Without RuntimeMaxSec a hung gptme process keeps the unit active forever"
    )
