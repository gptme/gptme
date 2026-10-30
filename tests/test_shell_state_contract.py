"""Document and exercise the foreground/explicit-background state boundary."""

import os
import shlex
from pathlib import Path

import pytest

from gptme.tools.shell import ShellSession, _workspace_cwd, instructions, tool
from gptme.tools.shell_background import reset_background_jobs, start_background_job


def test_shell_guidance_explains_explicit_background_isolation() -> None:
    assert "Python parent's cwd and environment" in instructions
    assert "Do not rely on prior foreground `cd`" in instructions
    assert "workspace/server sessions keep it separate" in instructions
    assert "automatic promotion" in instructions
    assert "Bash `&`" in instructions
    background = next(p for p in tool.parameters if p.name == "background")
    assert background.description is not None
    assert "separate shell" in background.description
    assert "cd/export" in background.description


@pytest.mark.parametrize("workspace", [True, False], ids=["workspace", "cli"])
def test_foreground_and_background_state_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace: bool
) -> None:
    parent = tmp_path / "parent"
    foreground = tmp_path / "foreground"
    checkout = tmp_path / "intended checkout"
    for directory in (parent, foreground, checkout):
        directory.mkdir()
    (checkout / "checkout-marker").write_text("intended-checkout", encoding="utf-8")
    monkeypatch.chdir(parent)
    monkeypatch.setenv("GPTME_TEST_PARENT_STATE", "parent-value")
    monkeypatch.delenv("GPTME_TEST_FOREGROUND_STATE", raising=False)
    # Construct in the caller's real context, then exercise the captured flag
    # without that context, as happens on a foreground-promotion worker thread.
    token = _workspace_cwd.set(str(parent) if workspace else None)
    try:
        shell = ShellSession(cwd=str(parent))
    finally:
        _workspace_cwd.reset(token)
    worker_token = _workspace_cwd.set(None)
    reset_background_jobs()
    try:
        assert (
            shell.run(
                f"cd {shlex.quote(str(foreground))}; "
                "export GPTME_TEST_FOREGROUND_STATE=foreground-value; "
                "GPTME_TEST_LOCAL_STATE=local-value",
                output=False,
            )[0]
            == 0
        )
        rc, out, err = shell.run(
            'printf "%s|%s|%s|%s" "$PWD" "$GPTME_TEST_PARENT_STATE" '
            '"$GPTME_TEST_FOREGROUND_STATE" "$GPTME_TEST_LOCAL_STATE"',
            output=False,
        )
        assert (rc, out, err) == (
            0,
            f"{foreground}|parent-value|foreground-value|local-value",
            "",
        )
        expected_job_cwd = parent if workspace else foreground
        assert Path.cwd() == expected_job_cwd
        job = start_background_job(
            'printf "%s|%s|%s|%s" "$PWD" "$GPTME_TEST_PARENT_STATE" '
            '"${GPTME_TEST_FOREGROUND_STATE-unset}" "${GPTME_TEST_LOCAL_STATE-unset}"'
        )
        assert job.process.wait(timeout=5) == 0
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=5)
        assert job.get_output() == (f"{expected_job_cwd}|parent-value|unset|unset", "")
        # An explicit job must carry its own path/state and validate its target.
        job = start_background_job(
            f"cd {shlex.quote(str(checkout))} && "
            "test -f checkout-marker && "
            "export GPTME_TEST_JOB_STATE=job-value && "
            'printf "%s|%s|" "$PWD" "$GPTME_TEST_JOB_STATE" && cat checkout-marker'
        )
        assert job.process.wait(timeout=5) == 0
        assert job._reader_thread is not None
        job._reader_thread.join(timeout=5)
        assert job.get_output() == (f"{checkout}|job-value|intended-checkout", "")
        assert "GPTME_TEST_JOB_STATE" not in os.environ
        assert shell.run(
            'printf "%s|%s" "$PWD" "${GPTME_TEST_JOB_STATE-unset}"', output=False
        ) == (0, f"{foreground}|unset", "")
    finally:
        reset_background_jobs()
        shell.close()
        _workspace_cwd.reset(worker_token)
