"""Tests for the ``--diff`` pair-programming context flag."""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli
from gptme.hooks import ConfirmationResult
from gptme.message import Message
from gptme.tools.base import ToolUse, using_current_tool_use
from gptme.util.ask_execute import execute_with_confirmation
from gptme.util.context import get_git_diff_context
from gptme.util.diff_suggestions import (
    record_diff_suggestion,
    track_diff_suggestions,
)

_chat_module = importlib.import_module("gptme.chat")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=gptme-test@example.com",
            "-c",
            "user.name=Test",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init")
    (tmp_path / "a.txt").write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ── get_git_diff_context unit tests ─────────────────────────────────────


def test_clean_worktree_returns_none(git_repo: Path):
    assert get_git_diff_context("HEAD") is None


def test_includes_filenames_and_diff(git_repo: Path):
    (git_repo / "a.txt").write_text("one\ntwo\n")
    result = get_git_diff_context("HEAD")
    assert result is not None
    assert "Changed files:" in result
    assert "a.txt" in result
    assert "+two" in result


def test_named_ref(git_repo: Path):
    (git_repo / "a.txt").write_text("one\ntwo\n")
    _git(git_repo, "commit", "-am", "change")
    (git_repo / "a.txt").write_text("one\ntwo\nthree\n")
    result = get_git_diff_context("HEAD~1")
    assert result is not None
    assert "+three" in result


def test_truncation(git_repo: Path):
    (git_repo / "a.txt").write_text("one\n" + "x" * 500 + "\n")
    result = get_git_diff_context("HEAD", max_chars=40)
    assert result is not None
    assert "truncated" in result


def test_invalid_ref_raises(git_repo: Path):
    with pytest.raises(RuntimeError):
        get_git_diff_context("does-not-exist-ref")


def test_non_git_dir_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError):
        get_git_diff_context("HEAD")


@pytest.mark.parametrize(
    ("confirmation", "decision"),
    [
        (ConfirmationResult.confirm(), "accepted"),
        (ConfirmationResult.skip("Declined by user"), "skipped"),
    ],
)
def test_tracks_edit_suggestion_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    confirmation: ConfirmationResult,
    decision: str,
):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("old\n")
    tool_use = ToolUse("patch", [str(target)], "old\n<<<<<<<\nnew\n>>>>>>>")

    monkeypatch.setattr("gptme.hooks.get_confirmation", lambda **_: confirmation)

    def _execute(content: str, path: Path | None):
        yield Message("system", f"applied to {path}")

    with (
        track_diff_suggestions(logdir, "main"),
        using_current_tool_use(tool_use),
    ):
        list(
            execute_with_confirmation(
                tool_use.content,
                tool_use.args,
                tool_use.kwargs,
                execute_fn=_execute,
                get_path_fn=lambda *_: target,
                preview_fn=lambda *_: "@@ -1 +1 @@\n-old\n+new",
            )
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["decision"] == decision
    assert event["confirmation_mode"] == "automatic"
    assert event["diff_ref"] == "main"
    assert event["targets"] == [str(target)]
    assert event["line_ranges"] == [
        {
            "file": str(target),
            "old_start": 1,
            "old_end": 1,
            "new_start": 1,
            "new_end": 1,
        }
    ]
    assert event["preview"] == "@@ -1 +1 @@\n-old\n+new"


def test_tracks_multiple_line_ranges_and_diff_header_paths(tmp_path: Path):
    logdir = tmp_path / "log"
    tool_use = ToolUse("morph", ["fallback.py"], "edit")
    preview = """--- a/example.py
+++ b/example.py
@@ -2,3 +2,4 @@
 unchanged
@@ -10,0 +12,2 @@ function
+inserted
"""

    with track_diff_suggestions(logdir, "HEAD"):
        record_diff_suggestion(
            tool_use,
            ConfirmationResult.confirm(),
            preview,
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["line_ranges"] == [
        {
            "file": "b/example.py",
            "old_start": 2,
            "old_end": 4,
            "new_start": 2,
            "new_end": 5,
        },
        {
            "file": "b/example.py",
            "old_start": 10,
            "old_end": 9,
            "new_start": 12,
            "new_end": 13,
        },
    ]


def test_unmappable_preview_keeps_raw_fallback(tmp_path: Path):
    logdir = tmp_path / "log"
    preview = "replacement content without a unified diff header"

    with track_diff_suggestions(logdir, "HEAD"):
        record_diff_suggestion(
            ToolUse("save", None, preview),
            ConfirmationResult.confirm(),
            preview,
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["line_ranges"] == []
    assert event["preview"] == preview


def test_tracking_failure_does_not_block_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    invalid_logdir = tmp_path / "not-a-directory"
    invalid_logdir.write_text("occupied")
    tool_use = ToolUse("patch", ["example.py"], "patch")
    executed: list[str] = []
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.confirm()
    )

    def _execute(content: str, path: Path | None):
        executed.append(content)
        yield Message("system", "applied")

    with (
        track_diff_suggestions(invalid_logdir, "HEAD"),
        using_current_tool_use(tool_use),
    ):
        list(
            execute_with_confirmation(
                tool_use.content,
                tool_use.args,
                tool_use.kwargs,
                execute_fn=_execute,
                get_path_fn=lambda *_: Path("example.py"),
            )
        )

    assert executed == ["patch"]


# ── CLI wiring ──────────────────────────────────────────────────────────


def _fake_config(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=["shell", "read"],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=workspace,
            stream=False,
            no_confirm=True,
            agent=None,
            gear=None,
        ),
        project=None,
    )


def test_diff_flag_injects_system_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        "gptme.config.setup_config_from_cli", lambda **_: _fake_config(tmp_path)
    )
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr("gptme.prompts.get_prompt", lambda **kwargs: [])
    monkeypatch.setattr(
        "gptme.util.context.get_git_diff_context", lambda ref: f"DIFF-CONTEXT:{ref}"
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)

    seen: dict[str, Any] = {}

    def _fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
        seen["initial_msgs"] = initial_msgs
        seen["logdir"] = logdir
        record_diff_suggestion(
            ToolUse("patch", ["example.py"], "old\n<<<<<<<\nnew\n>>>>>>>"),
            ConfirmationResult.confirm(),
            "@@ -1 +1 @@\n-old\n+new",
        )

    monkeypatch.setattr(_chat_module, "chat", _fake_chat)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--diff-ref", "main", "review this"], input="")
    assert result.exit_code == 0, result.output

    system_msgs = [m for m in seen["initial_msgs"] if m.role == "system"]
    assert any("DIFF-CONTEXT:main" in m.content for m in system_msgs)
    event = json.loads((seen["logdir"] / "diff-suggestions.jsonl").read_text())
    assert event["diff_ref"] == "main"


def test_diff_flag_bare_uses_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        "gptme.config.setup_config_from_cli", lambda **_: _fake_config(tmp_path)
    )
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr("gptme.prompts.get_prompt", lambda **kwargs: [])

    refs: list[str] = []

    def _record_ref(ref: str) -> None:
        refs.append(ref)

    monkeypatch.setattr("gptme.util.context.get_git_diff_context", _record_ref)
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)
    monkeypatch.setattr(_chat_module, "chat", lambda *a, **k: None)

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--diff", "review this"], input="")
    assert result.exit_code == 0, result.output
    assert refs == ["HEAD"]


def test_diff_error_is_usage_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    monkeypatch.setattr(
        "gptme.config.setup_config_from_cli", lambda **_: _fake_config(tmp_path)
    )
    monkeypatch.setattr("gptme.tools.init_tools", lambda _: [])
    monkeypatch.setattr("gptme.prompts.get_prompt", lambda **kwargs: [])
    monkeypatch.setattr(
        "gptme.util.context.get_git_diff_context",
        lambda ref: (_ for _ in ()).throw(RuntimeError("not a git repository")),
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)
    monkeypatch.setattr(
        _chat_module, "chat", lambda *a, **k: pytest.fail("chat should not run")
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--diff", "review this"], input="")
    assert result.exit_code != 0
    assert "not a git repository" in result.output
