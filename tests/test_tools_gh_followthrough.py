"""Offline regressions for gptme/gptme#5239.

1. A markdown block carrying the command in its body goes through the same
   native dispatch (and argument guards) as the fence-line and structured forms.
2. Native ``run view`` output gets the pass-through credential redaction.
3. ``pr view``'s Actions summary reads every check-run page.
"""

import json
import shlex
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from gptme.message import Message
from gptme.tools.gh import execute_gh
from gptme.util.gh import _get_github_actions_status

SHA = "a" * 40


def invoke(command: str, form: str) -> list[Message]:
    if form == "args":
        return list(execute_gh(None, shlex.split(command), None))
    if form == "structured":
        return list(execute_gh(None, None, {"command": command}))
    if form == "body":
        return list(execute_gh(command + "\n", [], None))
    assert form == "body-gh"
    return list(execute_gh(f"gh {command}\n", [], None))


FORMS = ["args", "structured", "body", "body-gh"]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("ref", ["o/r#42", "42 --repo o/r", "42 -R o/r"])
@pytest.mark.parametrize("suffix", ["--admin", "--auto=false", "--body x"])
def test_merge_guards_apply_to_every_form(form: str, ref: str, suffix: str) -> None:
    with (
        patch("gptme.tools.gh.merge_github_pr") as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("CLI")),
    ):
        messages = invoke(f"pr merge {ref} {suffix}", form)
    merge.assert_not_called()
    assert len(messages) == 1
    assert messages[0].content.startswith("Error:")


@pytest.mark.parametrize("form", ["body", "body-gh"])
def test_valid_merge_from_block_body_uses_native_handler(form: str) -> None:
    with (
        patch(
            "gptme.tools.gh.merge_github_pr",
            return_value={"success": True, "message": "synthetic", "sha": "m"},
        ) as merge,
        patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("CLI")),
    ):
        messages = invoke(f"pr merge o/r#42 --squash --match-head-commit {SHA}", form)
    merge.assert_called_once()
    assert merge.call_args.args == ("o", "r", "42")
    assert "synthetic" in messages[0].content


@pytest.mark.parametrize("form", ["body", "body-gh"])
def test_block_body_unknown_command_still_passes_through(form: str) -> None:
    calls: list[list[str]] = []

    def transport(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    with patch("gptme.tools.gh.subprocess.run", side_effect=transport):
        messages = invoke("repo view o/r --json name", form)
    assert calls == [["gh", "repo", "view", "o/r", "--json", "name"]]
    assert "ok" in messages[0].content


def test_block_body_parse_error_is_reported() -> None:
    with patch("gptme.tools.gh.subprocess.run", side_effect=AssertionError("CLI")):
        messages = list(execute_gh('gh pr view "unterminated\n', [], None))
    assert len(messages) == 1
    assert messages[0].content.startswith("Error parsing command")


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("repo", ["", " --repo o/r"])
def test_native_run_view_output_is_redacted(form: str, repo: str, monkeypatch) -> None:
    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "true")
    log = "step failed\nGITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
    with patch("gptme.tools.gh.get_github_run_logs", return_value=log) as logs:
        messages = invoke(f"run view 123{repo}", form)
    logs.assert_called_once()
    text = messages[0].content
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in text
    assert "GITHUB_TOKEN=[REDACTED]" in text
    assert "step failed" in text


def test_native_run_view_redaction_respects_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "false")
    log = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    with patch("gptme.tools.gh.get_github_run_logs", return_value=log):
        messages = invoke("run view 123", "args")
    assert log in messages[0].content


def _paged_transport(runs: list[dict], endpoints: list[str]):
    def transport(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        endpoint = cmd[2]
        endpoints.append(endpoint)
        query = parse_qs(urlparse(endpoint).query)
        size = int(query.get("per_page", [30])[0])
        page = int(query.get("page", [1])[0])
        data = {
            "total_count": len(runs),
            "check_runs": runs[(page - 1) * size : page * size],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    return transport


def test_pr_view_actions_status_reads_every_page() -> None:
    runs = [
        {"id": i, "name": f"check-{i}", "status": "completed", "conclusion": "success"}
        for i in range(150)
    ]
    runs[120]["conclusion"] = "failure"
    endpoints: list[str] = []
    with patch(
        "gptme.util.gh.subprocess.run", side_effect=_paged_transport(runs, endpoints)
    ):
        text = _get_github_actions_status("o", "r", SHA)
    assert text is not None
    assert "❌ check-120: failure" in text
    assert "149 checks success" in text
    assert len(endpoints) == 2


def test_pr_view_actions_status_caps_names_per_state() -> None:
    """Counts stay exact; passing names are capped harder than failures."""
    runs = [
        {"id": i, "name": f"check-{i}", "status": "completed", "conclusion": "success"}
        for i in range(150)
    ]
    for i in range(30):
        runs[100 + i]["conclusion"] = "failure"
    with patch("gptme.util.gh.subprocess.run", side_effect=_paged_transport(runs, [])):
        text = _get_github_actions_status("o", "r", SHA)
    assert text is not None
    failure_line, success_line = text.splitlines()
    assert failure_line.startswith("❌ 30 checks failure: check-100, ")
    assert "check-124" in failure_line
    assert "check-125" not in failure_line
    assert failure_line.endswith("(+5 more)")
    assert success_line.startswith("✅ 120 checks success: check-0, ")
    assert "check-4," in success_line
    assert "check-5," not in success_line
    assert success_line.endswith("(+115 more)")
    assert len(text) < 1000


def test_pr_view_actions_status_warns_on_incomplete_listing() -> None:
    """A page that ends early must not be summarized as the full picture."""
    runs = [
        {"id": i, "name": f"check-{i}", "status": "completed", "conclusion": "success"}
        for i in range(100)
    ]

    def transport(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        page = int(parse_qs(urlparse(cmd[2]).query).get("page", [1])[0])
        batch = runs if page == 1 else []
        data = {"total_count": 150, "check_runs": batch}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    with patch("gptme.util.gh.subprocess.run", side_effect=transport):
        text = _get_github_actions_status("o", "r", SHA)
    assert text is not None
    assert "CI status unavailable" in text
    assert "retry" in text


@pytest.mark.parametrize("form", FORMS)
def test_bare_merge_ref_resolves_against_session_workspace(
    form: str, tmp_path, monkeypatch
) -> None:
    """A bare PR number must resolve against the session checkout, which is the
    cwd the CLI pass-through used, not the host process cwd."""
    from gptme.util.workspace import workspace_scope

    seen_cwds: list[str | None] = []

    def remote(cmd: list[str], **kwargs: object) -> SimpleNamespace:
        seen_cwds.append(kwargs.get("cwd"))  # type: ignore[arg-type]
        # origin points at a fork; gh has selected its upstream as default.
        if cmd[0] == "gh":
            assert cmd == [
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner",
                "--jq",
                ".nameWithOwner",
            ]
            repo = "session/repo" if kwargs.get("cwd") == str(tmp_path) else "host/repo"
            return SimpleNamespace(returncode=0, stdout=repo + "\n", stderr="")
        return SimpleNamespace(
            returncode=0, stdout="git@github.com:fork/repo.git\n", stderr=""
        )

    with (
        workspace_scope(tmp_path),
        patch("gptme.util.gh.subprocess.run", side_effect=remote),
        patch(
            "gptme.tools.gh.merge_github_pr",
            return_value={"success": True, "message": "synthetic", "sha": "m"},
        ) as merge,
    ):
        invoke(f"pr merge 42 --squash --match-head-commit {SHA}", form)
    assert seen_cwds == [str(tmp_path)]
    assert merge.call_args.args == ("session", "repo", "42")


def test_run_log_redacted_before_truncation(monkeypatch) -> None:
    """Wherever the tail cut lands, no fragment of the credential survives.

    Redacting only the final output misses a token whose key name was clipped
    away, leaving the value (or part of it) the filter no longer recognizes.
    """
    from gptme.tools.gh import _redact_passthrough_output
    from gptme.util.gh import get_github_run_logs

    monkeypatch.setenv("GPTME_SHELL_REDACT_SECRETS", "true")
    secret = "ghp_" + "abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    meta = {
        "status": "completed",
        "conclusion": "failure",
        "jobs": [{"name": "build", "conclusion": "failure", "databaseId": 1}],
    }
    leaks: list[int] = []
    for pad in range(0, 2200, 5):
        log = f"setup {'x' * 3000} GITHUB_TOKEN={secret} {'y' * pad}\nError: failed\n"

        def transport(cmd: list[str], log: str = log, **kwargs: object):
            out = json.dumps(meta) if "--json" in cmd else log
            return SimpleNamespace(returncode=0, stdout=out, stderr="")

        with (
            patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh"),
            patch("gptme.util.gh.subprocess.run", side_effect=transport),
        ):
            text = get_github_run_logs("123", max_tokens=500)
        assert text is not None
        text = _redact_passthrough_output(text)  # what the tool shows
        if any(secret[i : i + 8] in text for i in range(len(secret) - 7)):
            leaks.append(pad)
    assert leaks == []


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("repo_flag", ["", " --repo o/r"])
@pytest.mark.parametrize("fallback", [False, True])
def test_native_run_view_uses_session_checkout(
    form, repo_flag, fallback, tmp_path
) -> None:
    from gptme.util.workspace import workspace_scope

    calls = []
    meta = {
        "status": "completed",
        "conclusion": "failure",
        "url": "https://github.com/session/repo/actions/runs/123",
        "jobs": [{"name": "build", "conclusion": "failure", "databaseId": 1}],
    }

    def transport(cmd, **kwargs):
        calls.append((cmd, kwargs.get("cwd")))
        if "--json" in cmd:
            return SimpleNamespace(returncode=0, stdout=json.dumps(meta), stderr="")
        if "--log-failed" in cmd and fallback:
            return SimpleNamespace(returncode=1, stdout="", stderr="unavailable")
        return SimpleNamespace(
            returncode=0, stdout="Error: synthetic failure", stderr=""
        )

    with (
        workspace_scope(tmp_path),
        patch("gptme.util.gh.shutil.which", return_value="/usr/bin/gh"),
        patch("gptme.util.gh.subprocess.run", side_effect=transport),
    ):
        messages = invoke(f"run view 123{repo_flag}", form)
    assert "synthetic failure" in messages[0].content
    assert len(calls) == (3 if fallback else 2)
    assert all(str(cwd) == str(tmp_path) for _, cwd in calls)
    for cmd, _ in calls[:2]:
        assert (cmd[-2:] == ["--repo", "o/r"]) == bool(repo_flag)


@pytest.mark.parametrize("output", ["", "invalid", "host/owner/repo"])
def test_bare_merge_fails_closed_when_gh_repo_is_unknown(output) -> None:
    with (
        patch(
            "gptme.tools.gh.subprocess.run", return_value=SimpleNamespace(stdout=output)
        ),
        patch("gptme.tools.gh.merge_github_pr") as merge,
    ):
        messages = invoke("pr merge 42 --squash", "body")
    merge.assert_not_called()
    assert "Use owner/repo#N or --repo owner/repo" in messages[0].content
