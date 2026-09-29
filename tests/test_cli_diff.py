"""Tests for the ``--diff`` pair-programming context flag."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli
from gptme.hooks import ConfirmationResult
from gptme.message import Message
from gptme.tools.base import ToolUse, using_current_tool_use
from gptme.tools.patch import DIVIDER, ORIGINAL, UPDATED, preview_patch
from gptme.util.ask_execute import execute_with_confirmation
from gptme.util.context import _read_untracked_bytes, get_git_diff_context
from gptme.util.diff_suggestions import (
    record_diff_suggestion,
    restore_diff_tracker,
    snapshot_diff_tracker,
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
    assert "untrusted repository data" in result
    assert "Changed files:" in result
    assert "a.txt" in result
    assert "+two" in result


def test_includes_untracked_files(git_repo: Path):
    (git_repo / "new.txt").write_text("fresh\n")
    result = get_git_diff_context("HEAD")
    assert result is not None
    assert "new.txt" in result
    assert "+fresh" in result


def test_skips_untracked_symlinks(git_repo: Path):
    (git_repo / ".gitignore").write_text(".env\n")
    _git(git_repo, "add", ".gitignore")
    _git(git_repo, "commit", "-m", "ignore env")
    (git_repo / ".env").write_text("SECRET=do-not-leak\n")
    (git_repo / "link.txt").symlink_to(".env")
    result = get_git_diff_context("HEAD")
    assert result is not None
    assert "link.txt" in result
    assert "symlink:" in result
    assert "SECRET=do-not-leak" not in result


def test_does_not_read_whole_large_untracked_file(git_repo: Path):
    (git_repo / "huge.txt").write_text("x" * 200_000 + "\nSHOULD_NOT_APPEAR\n")
    result = get_git_diff_context("HEAD", max_chars=2000)
    assert result is not None
    assert "huge.txt" in result
    assert "SHOULD_NOT_APPEAR" not in result
    assert len(result) < 4000


def test_non_regular_untracked_file_does_not_block(git_repo: Path):
    fifo = git_repo / "events.fifo"
    os.mkfifo(fifo)
    with pytest.raises(OSError, match="not a regular file"):
        _read_untracked_bytes(fifo, 100)


def test_skips_untracked_read_when_budget_exhausted(git_repo: Path):
    (git_repo / "first.txt").write_text("a" * 400 + "\n")
    (git_repo / "second.txt").write_text("SHOULD_NOT_APPEAR\n")
    result = get_git_diff_context("HEAD", max_chars=80)
    assert result is not None
    assert "SHOULD_NOT_APPEAR" not in result


def test_includes_untracked_non_ascii_filename(git_repo: Path):
    (git_repo / "café.txt").write_text("latte\n")
    result = get_git_diff_context("HEAD")
    assert result is not None
    assert "+latte" in result
    assert "café.txt" in result


def test_truncated_diff_keeps_closed_fence(git_repo: Path):
    (git_repo / "a.txt").write_text("one\n" + "x" * 5000 + "\n")
    result = get_git_diff_context("HEAD", max_chars=800)
    assert result is not None
    assert "truncated" in result
    assert result.count("````") >= 2
    assert result.count("````") % 2 == 0


def test_reads_untracked_from_git_toplevel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    sub = repo / "pkg"
    sub.mkdir(parents=True)
    _git(repo, "init")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")
    (sub / "new.txt").write_text("from-sub\n")
    monkeypatch.chdir(sub)
    result = get_git_diff_context("HEAD", cwd=sub)
    assert result is not None
    assert "new.txt" in result
    assert "+from-sub" in result


def test_uses_workspace_not_launch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launch = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    launch.mkdir()
    workspace.mkdir()
    _git(workspace, "init")
    (workspace / "a.txt").write_text("one\n")
    _git(workspace, "add", "a.txt")
    _git(workspace, "commit", "-m", "init")
    (workspace / "a.txt").write_text("one\ntwo\n")
    monkeypatch.chdir(launch)
    result = get_git_diff_context("HEAD", cwd=workspace)
    assert result is not None
    assert "+two" in result
    with pytest.raises(RuntimeError):
        get_git_diff_context("HEAD")


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


def test_dash_prefixed_ref_is_rejected(git_repo: Path, tmp_path: Path):
    sink = tmp_path / "should-not-be-written"
    with pytest.raises(RuntimeError, match="invalid git ref"):
        get_git_diff_context(f"--output={sink}")
    assert not sink.exists()


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
    assert event["execution_status"] == (
        "not_run" if decision == "skipped" else "applied"
    )
    assert event["confirmation_mode"] == "automatic"
    assert event["diff_ref"] == "main"
    assert event["targets"] == [str(target)]
    assert event["line_ranges"] == [
        {
            "file": str(target),
            "old_start": 1,
            "old_end": 1,
            "old_count": 1,
            "new_start": 1,
            "new_end": 1,
            "new_count": 1,
        }
    ]
    assert event["preview"] == "@@ -1 +1 @@\n-old\n+new"


def test_failed_edit_execution_is_distinct_from_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("old\n")
    tool_use = ToolUse("patch", [str(target)], "patch")

    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.confirm()
    )

    def _execute(content: str, path: Path | None):
        raise ValueError("original chunk not found")
        yield  # pragma: no cover

    with (
        track_diff_suggestions(logdir, "HEAD"),
        using_current_tool_use(tool_use),
    ):
        messages = list(
            execute_with_confirmation(
                tool_use.content,
                tool_use.args,
                tool_use.kwargs,
                execute_fn=_execute,
                get_path_fn=lambda *_: target,
            )
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["decision"] == "accepted"
    assert event["execution_status"] == "failed"
    assert event["execution_error"] == "original chunk not found"
    assert "Error during execution" in messages[-1].content


def test_failed_edit_message_is_not_recorded_as_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    logdir = tmp_path / "log"
    tool_use = ToolUse("patch_many", ["example.py"], "patch")
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.confirm()
    )

    def _execute(content: str, path: Path | None):
        yield Message("system", "Atomic patch aborted: original chunk not found")

    with (
        track_diff_suggestions(logdir, "HEAD"),
        using_current_tool_use(tool_use),
    ):
        list(
            execute_with_confirmation(
                tool_use.content,
                tool_use.args,
                tool_use.kwargs,
                execute_fn=_execute,
                get_path_fn=lambda *_: None,
            )
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["decision"] == "accepted"
    assert event["execution_status"] == "failed"
    assert event["execution_error"].startswith("Atomic patch aborted:")


def test_patch_ranges_are_resolved_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("before\nold value\nafter\n")
    tool_use = ToolUse("patch", [str(target)], "patch")
    monkeypatch.setattr(
        "gptme.hooks.get_confirmation", lambda **_: ConfirmationResult.confirm()
    )

    def _execute(content: str, path: Path | None):
        assert path is not None
        path.write_text("before\nnew value\nafter\n")
        yield Message("system", "Patch successfully applied")

    with (
        track_diff_suggestions(logdir, "HEAD"),
        using_current_tool_use(tool_use),
    ):
        list(
            execute_with_confirmation(
                tool_use.content,
                tool_use.args,
                tool_use.kwargs,
                execute_fn=_execute,
                get_path_fn=lambda *_: target,
                preview_fn=lambda *_: "-old value\n+new value",
            )
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert target.read_text() == "before\nnew value\nafter\n"
    assert event["execution_status"] == "applied"
    assert event["line_ranges"][0]["old_start"] == 2
    assert event["line_ranges"][0]["new_start"] == 2


def test_aborted_edit_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("old\n")
    tool_use = ToolUse("patch", [str(target)], "old\n<<<<<<<\nnew\n>>>>>>>")
    responses = [
        ConfirmationResult.edit("edited-once"),
        ConfirmationResult.edit("edited-again"),
    ]

    monkeypatch.setattr("gptme.hooks.get_confirmation", lambda **_: responses.pop(0))

    def _execute(content: str, path: Path | None):
        raise AssertionError("aborted edit must not execute")

    with (
        track_diff_suggestions(logdir, "HEAD"),
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
    assert event["decision"] == "skipped"
    assert event["edited_by_user"] is True


def test_records_from_inherited_worker_thread(tmp_path: Path):
    logdir = tmp_path / "log"
    tool_use = ToolUse("patch", ["example.py"], "patch")

    def _worker() -> None:
        restore_diff_tracker(tracker)
        record_diff_suggestion(
            tool_use,
            ConfirmationResult.confirm(),
            "@@ -1 +1 @@\n-old\n+new",
        )

    with track_diff_suggestions(logdir, "HEAD"):
        tracker = snapshot_diff_tracker()
        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["decision"] == "accepted"
    assert event["tool"] == "patch"


def test_identical_suggestions_get_unique_event_ids_and_stable_content_id(
    tmp_path: Path,
):
    logdir = tmp_path / "log"
    tool_use = ToolUse("patch", ["example.py"], "patch")

    with track_diff_suggestions(logdir, "HEAD"):
        for _ in range(2):
            record_diff_suggestion(
                tool_use,
                ConfirmationResult.confirm(),
                "@@ -1 +1 @@\n-old\n+new",
            )

    events = [
        json.loads(line)
        for line in (logdir / "diff-suggestions.jsonl").read_text().splitlines()
    ]
    assert events[0]["suggestion_id"] != events[1]["suggestion_id"]
    assert events[0]["content_id"] == events[1]["content_id"]


def test_unrelated_worker_does_not_use_active_tracker(tmp_path: Path):
    logdir = tmp_path / "log"
    tool_use = ToolUse("patch", ["example.py"], "patch")
    saw_ledger = []

    def _worker() -> None:
        record_diff_suggestion(
            tool_use,
            ConfirmationResult.confirm(),
            "@@ -1 +1 @@\n-old\n+new",
        )
        saw_ledger.append((logdir / "diff-suggestions.jsonl").exists())

    with track_diff_suggestions(logdir, "HEAD"):
        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()

    assert saw_ledger == [False]
    assert not (logdir / "diff-suggestions.jsonl").exists()


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
            "old_count": 3,
            "new_start": 2,
            "new_end": 5,
            "new_count": 4,
        },
        {
            "file": "b/example.py",
            "old_start": 10,
            "old_end": 10,
            "old_count": 0,
            "new_start": 12,
            "new_end": 13,
            "new_count": 2,
        },
    ]


def test_tracks_native_patch_preview_ranges_without_hunk_headers(tmp_path: Path):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("before\nold value\nafter\n")
    tool_use = ToolUse("patch", [str(target)], "unused")
    preview = preview_patch(
        "<<<<<<< ORIGINAL\nold value\n=======\nnew value\n>>>>>>> UPDATED",
        target,
    )
    assert preview is not None

    with track_diff_suggestions(logdir, "HEAD"):
        record_diff_suggestion(
            tool_use,
            ConfirmationResult.confirm(),
            preview,
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert event["line_ranges"] == [
        {
            "file": str(target),
            "old_start": 2,
            "old_end": 2,
            "old_count": 1,
            "new_start": 2,
            "new_end": 2,
            "new_count": 1,
        }
    ]


def test_native_patch_ranges_account_for_prior_hunk_line_delta(tmp_path: Path):
    logdir = tmp_path / "log"
    target = tmp_path / "example.py"
    target.write_text("before\nfirst\nmiddle\nsecond\nafter\n")
    patch = (
        f"{ORIGINAL}first{DIVIDER}first a\nfirst b{UPDATED}\n"
        f"{ORIGINAL}second{DIVIDER}replacement{UPDATED}"
    )
    preview = preview_patch(patch, target)
    assert preview is not None

    with track_diff_suggestions(logdir, "HEAD"):
        record_diff_suggestion(
            ToolUse("patch", [str(target)], patch),
            ConfirmationResult.confirm(),
            preview,
        )

    event = json.loads((logdir / "diff-suggestions.jsonl").read_text())
    assert [(r["old_start"], r["new_start"]) for r in event["line_ranges"]] == [
        (2, 2),
        (4, 5),
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


def test_diff_flag_injects_untrusted_user_message(
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
        "gptme.util.context.get_git_diff_context",
        lambda ref, **kwargs: f"DIFF-CONTEXT:{ref}",
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
    user_msgs = [m for m in seen["initial_msgs"] if m.role == "user"]
    assert any("untrusted repository data" in m.content for m in system_msgs)
    assert any("DIFF-CONTEXT:main" in m.content for m in user_msgs)
    assert not any("DIFF-CONTEXT:main" in m.content for m in system_msgs)
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

    def _record_ref(ref: str, **kwargs: Any) -> None:
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
        lambda ref, **kwargs: (_ for _ in ()).throw(
            RuntimeError("not a git repository")
        ),
    )
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)
    monkeypatch.setattr(
        _chat_module, "chat", lambda *a, **k: pytest.fail("chat should not run")
    )

    runner = CliRunner()
    result = runner.invoke(cli.main, ["--diff", "review this"], input="")
    assert result.exit_code != 0
    assert "not a git repository" in result.output
