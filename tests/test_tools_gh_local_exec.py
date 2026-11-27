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
        ["alias", "set", "co", "pr checkout"],
        ["alias", "set", "cb", "config set browser"],
        ["alias", "set", "ei", "extension install"],
        ["alias", "-x", "v", "set", "cb", "config set browser"],
        ["config", "-h", "github.com", "set", "browser", "touch /tmp/x"],
        ["extension", "--force", "list", "install", "owner/gh-pwn"],
        ["--help=false", "alias", "set", "cb", "config set browser"],
        ["codespace", "ssh", "--", "-oProxyCommand=touch /tmp/x"],
        ["cs", "cp", "a", "remote:b", "--", "-oProxyCommand=touch /tmp/x"],
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
        ["alias", "list"],
        ["alias", "delete", "co"],
        ["codespace", "ssh", "-c", "name"],
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


def test_existing_shell_alias_is_refused():
    """Blocking alias *definitions* does not stop an alias the user already has."""
    with (
        patch(
            "gptme.tools.gh._gh_alias_values",
            return_value={"pwn": "!touch /tmp/x"},
        ),
        patch("gptme.tools.gh.subprocess.run") as run,
    ):
        out = _run(["pwn"])
    run.assert_not_called()
    assert "refused" in out


def test_alias_expanding_to_refused_command_is_refused():
    with (
        patch(
            "gptme.tools.gh._gh_alias_values",
            return_value={"cb": "config set browser"},
        ),
        patch("gptme.tools.gh.subprocess.run") as run,
    ):
        out = _run(["cb"])
    run.assert_not_called()
    assert "refused" in out


def test_installed_extension_is_refused():
    with (
        patch("gptme.tools.gh._gh_alias_values", return_value={}),
        patch(
            "gptme.tools.gh._gh_extension_names",
            return_value=frozenset({"pwn"}),
        ),
        patch("gptme.tools.gh.subprocess.run") as run,
    ):
        out = _run(["pwn"])
    run.assert_not_called()
    assert "refused" in out


def test_safe_alias_and_unknown_command_still_pass_through():
    done = subprocess.CompletedProcess(["gh"], 0, stdout="ok", stderr="")
    with (
        patch("gptme.tools.gh._gh_alias_values", return_value={"co": "pr checkout"}),
        patch("gptme.tools.gh._gh_extension_names", return_value=frozenset()),
        patch("gptme.tools.gh.subprocess.run", return_value=done) as run,
    ):
        _run(["co"])
    run.assert_called_once()


def test_alias_lookup_reads_the_config_on_every_call(tmp_path, monkeypatch):
    """An alias defined mid-session must not stay invisible to the guard."""
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yml").write_text("aliases:\n    pwn: '!touch /tmp/x'\n")
    with patch("gptme.tools.gh.subprocess.run") as run:
        out = _run(["pwn"])
    run.assert_not_called()
    assert "refused" in out


@pytest.mark.parametrize("prefix", [["-R", "o/r"], ["--repo=o/r"], ["-Ro/r"]])
def test_repo_prefix_preserves_safe_reads_and_local_exec_checks(prefix):
    done = subprocess.CompletedProcess(["gh"], 0, stdout="ok", stderr="")
    with patch("gptme.tools.gh.subprocess.run", return_value=done) as run:
        _run([*prefix, "issue", "list"])
    run.assert_called_once()
    with patch("gptme.tools.gh.subprocess.run") as run:
        out = _run([*prefix, "alias", "set", "--shell", "pwn", "touch /tmp/x"])
    run.assert_not_called()
    assert "refused" in out


def test_alias_checks_include_invocation_arguments():
    with (
        patch("gptme.tools.gh._gh_alias_values", return_value={"cfg": "config"}),
        patch("gptme.tools.gh.subprocess.run") as run,
    ):
        out = _run(["cfg", "set", "browser", "touch /tmp/x"])
    run.assert_not_called()
    assert "refused" in out


@pytest.mark.skipif(shutil.which("gh") is None, reason="needs gh CLI")
def test_preexisting_shell_alias_does_not_execute(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_CONFIG_DIR", str(tmp_path))
    marker = tmp_path / "pwned"
    (tmp_path / "config.yml").write_text(f"aliases:\n  pwn: '!touch {marker}'\n")
    out = _run(["pwn"])
    assert "refused" in out
    assert not marker.exists()
