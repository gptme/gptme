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
def test_native_run_view_output_is_redacted(form: str, repo: str) -> None:
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


def test_pr_view_actions_status_omitted_on_incomplete_listing() -> None:
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
        assert _get_github_actions_status("o", "r", SHA) is None
