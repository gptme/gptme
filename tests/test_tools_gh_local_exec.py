"""The gh tool must not hand the model unconfirmed local code execution.

The gh tool never asks for confirmation, and its pass-through forwards any
subcommand. Some gh subcommands run local programs: shell aliases, extensions,
and the browser/editor/pager settings. Through the gh tool those would bypass
the shell tool's confirmation, allowlist and guardrails.
"""

import shutil
import subprocess
from unittest.mock import patch

import pytest

from gptme.tools.gh import execute_gh


def _run(args: list[str] | None = None, code: str | None = None) -> str:
    return "\n".join(m.content for m in execute_gh(code, args, None))


@pytest.mark.parametrize(
    "args",
    [
        ["alias", "set", "--shell", "pwn", "touch /tmp/x"],
        ["alias", "set", "-s", "pwn", "touch /tmp/x"],
        ["alias", "set", "--clobber", "-s", "pwn", "touch /tmp/x"],
        ["alias", "set", "pwn", "!touch /tmp/x"],
        ["alias", "set", "pwn", "-"],
        ["alias", "import", "aliases.yml"],
        ["extension", "install", "owner/gh-pwn"],
        ["ext", "exec", "pwn"],
        ["extensions", "upgrade", "--all"],
        ["config", "set", "browser", "touch /tmp/x"],
        ["config", "set", "-h", "github.com", "editor", "touch /tmp/x"],
        ["config", "set", "pager", "touch /tmp/x"],
        ["repo", "clone", "o/r", "--", "-c", "core.sshCommand=touch /tmp/x"],
        ["repo", "fork", "o/r", "--clone", "--", "--config=core.sshCommand=x"],
        ["gist", "clone", "abc", "--", "-c", "core.fsmonitor=touch /tmp/x"],
    ],
)
def test_local_program_subcommands_are_refused(args):
    with patch("gptme.tools.gh.subprocess.run") as run:
        out = _run(args)
    run.assert_not_called()
    assert "refused" in out


def test_code_form_is_refused_too():
    with patch("gptme.tools.gh.subprocess.run") as run:
        out = _run(code="gh alias set --shell pwn 'touch /tmp/x'")
    run.assert_not_called()
    assert "refused" in out


@pytest.mark.parametrize(
    "args",
    [
        ["alias", "set", "co", "pr checkout"],
        ["alias", "list"],
        ["extension", "list"],
        ["config", "set", "git_protocol", "ssh"],
        ["config", "get", "browser"],
        ["issue", "list", "-s", "open"],
        ["repo", "clone", "o/r"],
        ["repo", "clone", "o/r", "dir"],
    ],
)
def test_ordinary_subcommands_still_pass_through(args):
    done = subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")
    with patch("gptme.tools.gh.subprocess.run", return_value=done) as run:
        _run(args)
    run.assert_called_once()


@pytest.mark.skipif(shutil.which("gh") is None, reason="needs gh CLI")
def test_shell_alias_does_not_execute(tmp_path, monkeypatch):
    """End to end with the real CLI: defining and invoking a shell alias."""
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path / "ghcfg"))
    monkeypatch.setenv("GH_NO_UPDATE_NOTIFIER", "1")
    monkeypatch.setenv("GH_PROMPT_DISABLED", "1")
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "pwned"

    _run(["alias", "set", "--shell", "pwn", f"touch {marker}"])
    _run(["pwn"])

    assert not marker.exists()
