"""Tests for allowlisted agent fork-script execution.

PUT /api/v2/agents and gptme-agent create must not execute a caller-supplied
command string. Only a relative ./scripts/*.sh from the cloned template is
run, as argv [script, dest_path, agent_name].
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.agent.workspace import (
    DEFAULT_FORK_SCRIPT,
    WorkspaceError,
    create_workspace_from_template,
    parse_fork_script,
)


@pytest.mark.parametrize(
    "command",
    [
        DEFAULT_FORK_SCRIPT,
        "./scripts/fork.sh {path} {name}",
        "./scripts/fork.sh {name} {path}",
        "scripts/fork.sh",
        "./scripts/custom-setup.sh",
    ],
)
def test_parse_fork_script_allows_template_scripts(command: str):
    script = parse_fork_script(command)
    assert script.endswith(".sh")
    assert "scripts/" in script.replace("\\", "/")


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'touch pwned'",
        "echo fork",
        "/bin/bash",
        "./scripts/../evil.sh",
        "python3 -c 'print(1)'",
        "scripts/fork.sh; id",
        "",
        "   ",
    ],
)
def test_parse_fork_script_rejects_arbitrary_commands(command: str):
    with pytest.raises(WorkspaceError, match="fork_command must be a relative script"):
        parse_fork_script(command)


@pytest.mark.parametrize(
    "command",
    [
        "./scripts/custom.sh --flag value",
        "./scripts/fork.sh /tmp/agent bob",
        "./scripts/fork.sh {path}",
        "./scripts/fork.sh {path} {name} --force",
        "./scripts/fork.sh {path} {name} {path}",
        "./scripts/fork.sh {path} {path}",
    ],
)
def test_parse_fork_script_rejects_unsupported_extra_tokens(command: str):
    with pytest.raises(WorkspaceError, match="extra arguments are not supported"):
        parse_fork_script(command)


def test_create_workspace_rejects_bash_c_before_clone(tmp_path: Path):
    dest = tmp_path / "new-agent"
    with (
        patch("gptme.agent.workspace.subprocess.run") as mock_run,
        pytest.raises(WorkspaceError, match="fork_command must be a relative script"),
    ):
        create_workspace_from_template(
            dest,
            "test-agent",
            fork_command="bash -c 'touch pwned'",
        )
    mock_run.assert_not_called()
    assert not dest.exists()


def test_create_workspace_runs_default_fork_script_as_argv(tmp_path: Path):
    dest = tmp_path / "new-agent"
    recorded: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        recorded.append(list(cmd))
        if len(cmd) >= 2 and cmd[1] == "clone":
            clone_dir = Path(cmd[-1])
            script = clone_dir / "scripts" / "fork.sh"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("#!/bin/sh\n")
            script.chmod(0o755)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    with (
        patch("gptme.agent.workspace.subprocess.run", side_effect=fake_run),
        patch("gptme.agent.workspace._merge_project_config"),
    ):
        create_workspace_from_template(
            dest,
            "test-agent",
            fork_command="./scripts/fork.sh {path} {name}",
        )

    fork_calls = [cmd for cmd in recorded if str(cmd[0]).endswith("/scripts/fork.sh")]
    assert len(fork_calls) == 1
    argv = fork_calls[0]
    assert argv[1] == str(dest)
    assert argv[2] == "test-agent"
