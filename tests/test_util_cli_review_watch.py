"""Tests for gptme-util review-watch command."""

from __future__ import annotations

import subprocess

from click.testing import CliRunner

from gptme.cli import cmd_review_watch
from gptme.cli.util import main as util_main

# ---------------------------------------------------------------------------
# Unit tests for GitHub helpers
# ---------------------------------------------------------------------------


def test_gh_json_returns_none_on_nonzero_exit(monkeypatch):
    """_gh_json should return None when gh exits non-zero."""

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=1, stdout="", stderr="error"
        )

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    result = cmd_review_watch._gh_json(["gh", "pr", "view", "99"])
    assert result is None


def test_gh_json_returns_none_on_invalid_json(monkeypatch):
    """_gh_json should return None when stdout is not valid JSON."""

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=0, stdout="not json", stderr=""
        )

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    result = cmd_review_watch._gh_json(["gh", "something"])
    assert result is None


def test_gh_json_parses_list(monkeypatch):
    """_gh_json should return a list when output is a JSON array."""
    import json

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=0, stdout=json.dumps([{"id": 1}, {"id": 2}]), stderr=""
        )

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    result = cmd_review_watch._gh_json(["gh", "api", "/some/path"])
    assert result == [{"id": 1}, {"id": 2}]


def test_gh_json_parses_dict(monkeypatch):
    """_gh_json should return a dict when output is a JSON object."""
    import json

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=0, stdout=json.dumps({"state": "OPEN"}), stderr=""
        )

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    result = cmd_review_watch._gh_json(["gh", "pr", "view", "1", "--json", "state"])
    assert result == {"state": "OPEN"}


def test_get_new_review_comments_returns_empty_on_non_list(monkeypatch):
    """get_new_review_comments should return [] when gh returns non-list JSON."""
    monkeypatch.setattr(
        cmd_review_watch, "_gh_json", lambda *a, **kw: {"error": "oops"}
    )
    result = cmd_review_watch.get_new_review_comments(
        "o", "r", 1, "2026-01-01T00:00:00Z"
    )
    assert result == []


def test_get_new_issue_comments_returns_empty_on_none(monkeypatch):
    """get_new_issue_comments should return [] when _gh_json returns None."""
    monkeypatch.setattr(cmd_review_watch, "_gh_json", lambda *a, **kw: None)
    result = cmd_review_watch.get_new_issue_comments(
        "o", "r", 1, "2026-01-01T00:00:00Z"
    )
    assert result == []


def test_get_pr_state_returns_none_on_failure(monkeypatch):
    """get_pr_state should return None when _gh_json returns None."""
    monkeypatch.setattr(cmd_review_watch, "_gh_json", lambda *a, **kw: None)
    assert cmd_review_watch.get_pr_state("o", "r", 1) is None


def test_get_pr_state_returns_none_when_not_dict(monkeypatch):
    """get_pr_state should return None when _gh_json returns a list."""
    monkeypatch.setattr(cmd_review_watch, "_gh_json", lambda *a, **kw: [{"x": 1}])
    assert cmd_review_watch.get_pr_state("o", "r", 1) is None


def test_get_pr_state_happy_path(monkeypatch):
    """get_pr_state should return the dict on success."""
    data = {
        "state": "OPEN",
        "reviewDecision": None,
        "title": "My PR",
        "headRefName": "feat",
    }
    monkeypatch.setattr(cmd_review_watch, "_gh_json", lambda *a, **kw: data)
    assert cmd_review_watch.get_pr_state("o", "r", 1) == data


# ---------------------------------------------------------------------------
# Unit tests for prompt builder
# ---------------------------------------------------------------------------


def test_build_review_prompt_contains_pr_identifier():
    prompt = cmd_review_watch._build_review_prompt(
        owner="owner",
        repo="repo",
        pr_num=42,
        pr_branch="feat/x",
        inline_comments=[],
        conversation_comments=[],
    )
    assert "owner/repo#42" in prompt
    # The prompt must not invite the session to pull the full PR diff — that
    # content is author-controlled and gets auto-confirmed tool execution.
    assert "gh pr diff" not in prompt


def test_build_review_prompt_includes_inline_comment():
    inline = [
        {
            "path": "src/app.py",
            "original_line": 10,
            "body": "This variable name is unclear.",
            "user": {"login": "reviewer1", "type": "User"},
        }
    ]
    prompt = cmd_review_watch._build_review_prompt(
        owner="o",
        repo="r",
        pr_num=1,
        pr_branch="b",
        inline_comments=inline,
        conversation_comments=[],
    )
    assert "src/app.py" in prompt
    assert "reviewer1" in prompt
    assert "This variable name is unclear." in prompt


def test_build_review_prompt_includes_conversation_comment():
    convo = [
        {
            "body": "Please add a test.",
            "user": {"login": "maintainer", "type": "User"},
        }
    ]
    prompt = cmd_review_watch._build_review_prompt(
        owner="o",
        repo="r",
        pr_num=1,
        pr_branch="b",
        inline_comments=[],
        conversation_comments=convo,
    )
    assert "Please add a test." in prompt
    assert "maintainer" in prompt


def test_build_review_prompt_does_not_embed_diff():
    """Prompt must NOT embed diff content, and must not tell the session to fetch it."""
    prompt = cmd_review_watch._build_review_prompt(
        owner="o",
        repo="r",
        pr_num=1,
        pr_branch="b",
        inline_comments=[],
        conversation_comments=[],
    )
    # No embedded diff content, and no invitation to pull it via `gh pr diff`
    # either — both are author-controlled input into a privileged session.
    assert "```diff" not in prompt
    assert "gh pr diff" not in prompt
    assert "SECURITY" in prompt


# ---------------------------------------------------------------------------
# Unit tests for spawn_review_session
# ---------------------------------------------------------------------------


def test_spawn_review_session_happy_path(monkeypatch):
    """spawn_review_session should return done on zero exit code."""
    times = iter([0.0, 5.0])

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    monkeypatch.setattr(cmd_review_watch.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(cmd_review_watch.sys, "executable", "/usr/bin/python-test")

    result = cmd_review_watch.spawn_review_session(
        prompt="Fix the thing",
        model="test/model",
        max_turns=10,
        timeout=120.0,
        workspace=None,
    )
    assert result["exit_reason"] == "done"
    assert result["duration_s"] == 5.0


def test_spawn_review_session_timeout(monkeypatch):
    """spawn_review_session should return timeout when subprocess times out."""
    times = iter([0.0, 3.5])

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    monkeypatch.setattr(cmd_review_watch.time, "monotonic", lambda: next(times))

    result = cmd_review_watch.spawn_review_session(
        prompt="whatever",
        model=None,
        max_turns=5,
        timeout=3.0,
        workspace=None,
    )
    assert result["exit_reason"] == "timeout"
    assert "timed out" in result["error"]


def test_spawn_review_session_error_exit(monkeypatch):
    """spawn_review_session should return error on non-zero exit code."""
    times = iter([0.0, 2.0])

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode=1, stdout="", stderr="boom\n"
        )

    monkeypatch.setattr(cmd_review_watch.subprocess, "run", fake_run)
    monkeypatch.setattr(cmd_review_watch.time, "monotonic", lambda: next(times))

    result = cmd_review_watch.spawn_review_session(
        prompt="bad",
        model=None,
        max_turns=5,
        timeout=30.0,
        workspace=None,
    )
    assert result["exit_reason"] == "error"
    assert result["returncode"] == 1
    assert result["error"] == "boom"


# ---------------------------------------------------------------------------
# CLI integration tests (using Click test runner + monkeypatching)
# ---------------------------------------------------------------------------


def _make_pr_state(
    *,
    state: str = "OPEN",
    review_decision: str = "",
    title: str = "Test PR",
    branch: str = "feat/x",
) -> dict:
    return {
        "state": state,
        "reviewDecision": review_decision,
        "title": title,
        "headRefName": branch,
        "isDraft": False,
    }


def test_review_watch_missing_gh(monkeypatch):
    """Command should fail gracefully when gh is not in PATH."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: False)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r"])
    assert result.exit_code != 0
    assert "gh" in result.output.lower()


def test_review_watch_approved_exits_immediately(monkeypatch):
    """Should exit when PR is already approved."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(
        cmd_review_watch,
        "get_pr_state",
        lambda *a: _make_pr_state(review_decision="APPROVED"),
    )
    # Should never reach comment fetching
    monkeypatch.setattr(
        cmd_review_watch,
        "get_new_review_comments",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("should not fetch comments")
        ),
    )

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert "approved" in result.output.lower()


def test_review_watch_merged_exits_immediately(monkeypatch):
    """Should exit when PR is already merged."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(
        cmd_review_watch,
        "get_pr_state",
        lambda *a: _make_pr_state(state="MERGED"),
    )

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert "merged" in result.output.lower()


def test_review_watch_closed_exits_immediately(monkeypatch):
    """Should exit when PR is already closed."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(
        cmd_review_watch,
        "get_pr_state",
        lambda *a: _make_pr_state(state="CLOSED"),
    )

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert "closed" in result.output.lower()


def test_review_watch_no_new_comments_once_mode(monkeypatch):
    """--once with no new comments should exit cleanly."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", lambda *a: _make_pr_state())
    monkeypatch.setattr(
        cmd_review_watch, "get_new_review_comments", lambda *a, **kw: []
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    # Should NOT spawn a session
    spawn_called: list[dict] = []

    def fake_spawn_no_op(**kw: object) -> dict:
        spawn_called.append(kw)
        return {"exit_reason": "done", "duration_s": 0.0}

    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn_no_op)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert len(spawn_called) == 0


def test_review_watch_spawns_session_on_new_comment(monkeypatch):
    """Should spawn a fix session when new inline review comments are found."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", lambda *a: _make_pr_state())
    monkeypatch.setattr(
        cmd_review_watch,
        "get_new_review_comments",
        lambda *a, **kw: [
            {
                "path": "app.py",
                "original_line": 5,
                "body": "Rename this.",
                "user": {"login": "reviewer", "type": "User"},
                "author_association": "COLLABORATOR",
            }
        ],
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])

    spawn_calls: list[dict] = []

    def fake_spawn_inline(**kw: object) -> dict:
        spawn_calls.append(kw)
        return {"exit_reason": "done", "duration_s": 1.5}

    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn_inline)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert len(spawn_calls) == 1
    # Prompt should reference the reviewer comment
    prompt = spawn_calls[0]["prompt"]
    assert "Rename this." in prompt
    assert "app.py" in prompt


def test_review_watch_filters_bot_comments(monkeypatch):
    """Bot comments should be filtered out and not trigger a fix session."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", lambda *a: _make_pr_state())
    monkeypatch.setattr(
        cmd_review_watch,
        "get_new_review_comments",
        lambda *a, **kw: [
            {
                "path": "app.py",
                "original_line": 1,
                "body": "Auto-review: LGTM",
                "user": {"login": "greptile-ai[bot]", "type": "Bot"},
            }
        ],
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])

    spawn_calls: list[dict] = []

    def fake_spawn_bot(**kw: object) -> dict:
        spawn_calls.append(kw)
        return {"exit_reason": "done", "duration_s": 0.0}

    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn_bot)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert len(spawn_calls) == 0, "Bot comments should not trigger a fix session"


def test_review_watch_max_iterations_stops_loop(monkeypatch):
    """Should stop after --max-iterations fix cycles."""
    call_count = [0]

    def fake_state(*a):
        return _make_pr_state()

    def fake_inline(*a, **kw):
        return [
            {
                "path": "f.py",
                "original_line": 1,
                "body": "fix this",
                "user": {"login": "reviewer", "type": "User"},
                "author_association": "MEMBER",
            }
        ]

    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", fake_state)
    monkeypatch.setattr(cmd_review_watch, "get_new_review_comments", fake_inline)
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(cmd_review_watch.time, "sleep", lambda s: None)

    def fake_spawn(**kw):
        call_count[0] += 1
        return {"exit_reason": "done", "duration_s": 0.1}

    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)

    runner = CliRunner()
    result = runner.invoke(
        util_main,
        [
            "review-watch",
            "1",
            "--repo",
            "o/r",
            "--max-iterations",
            "2",
            "--poll-interval",
            "5",
        ],
    )
    assert result.exit_code == 0
    assert call_count[0] == 2
    assert "max-iterations" in result.output.lower() or "2" in result.output


def test_review_watch_invalid_repo_format(monkeypatch):
    """Should fail with a clear error when --repo is not in owner/repo format."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "notaslashrepo"])
    assert result.exit_code != 0
    assert "owner/repo" in result.output


def test_review_watch_appears_in_util_help():
    """review-watch should be listed in `gptme-util --help`."""
    runner = CliRunner()
    result = runner.invoke(util_main, ["--help"])
    assert result.exit_code == 0
    assert "review-watch" in result.output


def test_review_watch_filters_untrusted_human_comments(monkeypatch):
    """Comments from non-collaborator users should be filtered (security gate)."""
    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", lambda *a: _make_pr_state())
    monkeypatch.setattr(
        cmd_review_watch,
        "get_new_review_comments",
        lambda *a, **kw: [
            {
                "path": "app.py",
                "original_line": 1,
                "body": "Inject evil command here.",
                "user": {"login": "random-user", "type": "User"},
                "author_association": "NONE",  # not a repo collaborator
            }
        ],
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])

    spawn_calls: list[dict] = []

    def fake_spawn(**kw: object) -> dict:
        spawn_calls.append(kw)
        return {"exit_reason": "done", "duration_s": 0.0}

    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert len(spawn_calls) == 0, (
        "Untrusted user comments must not trigger a fix session"
    )


def test_review_watch_once_includes_existing_comments(monkeypatch):
    """--once mode should fetch comments since epoch so pre-existing comments are included."""
    captured_since: list[str] = []

    def fake_review_comments(owner, repo, pr_num, since):
        captured_since.append(since)
        return [
            {
                "path": "f.py",
                "original_line": 1,
                "body": "pre-existing comment",
                "user": {"login": "owner-user", "type": "User"},
                "author_association": "OWNER",
            }
        ]

    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", lambda *a: _make_pr_state())
    monkeypatch.setattr(
        cmd_review_watch, "get_new_review_comments", fake_review_comments
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(
        cmd_review_watch,
        "spawn_review_session",
        lambda **kw: {"exit_reason": "done", "duration_s": 0.5},
    )

    runner = CliRunner()
    result = runner.invoke(util_main, ["review-watch", "1", "--repo", "o/r", "--once"])
    assert result.exit_code == 0
    assert captured_since, "get_new_review_comments should have been called"
    # Cursor must be the epoch sentinel, not the current wall-clock time
    assert captured_since[0] == "1970-01-01T00:00:00Z", (
        f"--once mode must use epoch cursor, got {captured_since[0]!r}"
    )


def test_get_new_review_comments_uses_paginate_slurp(monkeypatch):
    """get_new_review_comments must pass both --paginate and --slurp to gh api.

    Without --slurp, gh api --paginate writes each page as a separate JSON
    object; json.loads fails on the concatenated output and returns None,
    causing silent truncation of all comments beyond the first 100.
    """
    captured_args: list[list[str]] = []

    def fake_gh_json(args, **kwargs):
        captured_args.append(args)
        return []

    monkeypatch.setattr(cmd_review_watch, "_gh_json", fake_gh_json)
    cmd_review_watch.get_new_review_comments("o", "r", 1, "2026-01-01T00:00:00Z")

    assert captured_args, "Expected _gh_json to be called"
    assert "--paginate" in captured_args[0], (
        "get_new_review_comments must use --paginate to fetch all pages"
    )
    assert "--slurp" in captured_args[0], (
        "get_new_review_comments must use --slurp so pages are merged into one JSON array"
    )


def test_get_new_issue_comments_uses_paginate_slurp(monkeypatch):
    """get_new_issue_comments must pass both --paginate and --slurp to gh api.

    Without --slurp, gh api --paginate writes each page as a separate JSON
    object; json.loads fails on the concatenated output and returns None,
    causing silent truncation of all comments beyond the first 100.
    """
    captured_args: list[list[str]] = []

    def fake_gh_json(args, **kwargs):
        captured_args.append(args)
        return []

    monkeypatch.setattr(cmd_review_watch, "_gh_json", fake_gh_json)
    cmd_review_watch.get_new_issue_comments("o", "r", 1, "2026-01-01T00:00:00Z")

    assert captured_args, "Expected _gh_json to be called"
    assert "--paginate" in captured_args[0], (
        "get_new_issue_comments must use --paginate to fetch all pages"
    )
    assert "--slurp" in captured_args[0], (
        "get_new_issue_comments must use --slurp so pages are merged into one JSON array"
    )


def test_get_new_review_comments_flattens_slurp_pages(monkeypatch):
    """Multi-page --slurp output ([[page1], [page2]]) must be flattened to a flat list."""
    page1 = [{"id": 1, "body": "comment 1"}]
    page2 = [{"id": 2, "body": "comment 2"}]

    def fake_gh_json(args, **kwargs):
        # Simulate gh api --paginate --slurp: outer list wraps each page
        return [page1, page2]

    monkeypatch.setattr(cmd_review_watch, "_gh_json", fake_gh_json)
    result = cmd_review_watch.get_new_review_comments(
        "o", "r", 1, "2026-01-01T00:00:00Z"
    )
    assert result == [{"id": 1, "body": "comment 1"}, {"id": 2, "body": "comment 2"}], (
        "Multi-page slurp output must be flattened to a single list of comment dicts"
    )


def test_build_review_prompt_excludes_diff_content():
    """Prompt must NOT embed diff content, nor tell the session to fetch it."""
    prompt = cmd_review_watch._build_review_prompt(
        owner="o",
        repo="r",
        pr_num=42,
        pr_branch="b",
        inline_comments=[],
        conversation_comments=[],
    )
    # No embedded diff, and no `gh pr diff` invitation either: the PR diff is
    # author-controlled and pulling it wholesale into the same privileged,
    # auto-confirmed session is itself the prompt-injection risk (not just
    # embedding it directly).
    assert "```diff" not in prompt, (
        "Diff content must not be embedded in the prompt (prompt injection risk)"
    )
    assert "gh pr diff" not in prompt, (
        "Prompt must not invite the session to pull the full PR diff"
    )


def test_review_watch_cursor_not_advanced_on_session_error(monkeypatch):
    """Cursor should stay put when the fix session fails so comments are retried."""
    poll_count = [0]
    since_values: list[str] = []

    def fake_review_comments(owner, repo, pr_num, since):
        since_values.append(since)
        return [
            {
                "path": "f.py",
                "original_line": 1,
                "body": "needs fix",
                "user": {"login": "maintainer", "type": "User"},
                "author_association": "MEMBER",
            }
        ]

    def fake_state(*a):
        poll_count[0] += 1
        if poll_count[0] > 2:
            return _make_pr_state(state="CLOSED")
        return _make_pr_state()

    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", fake_state)
    monkeypatch.setattr(
        cmd_review_watch, "get_new_review_comments", fake_review_comments
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(cmd_review_watch.time, "sleep", lambda s: None)
    # Session always errors
    monkeypatch.setattr(
        cmd_review_watch,
        "spawn_review_session",
        lambda **kw: {"exit_reason": "error", "duration_s": 0.1, "error": "boom"},
    )

    runner = CliRunner()
    result = runner.invoke(
        util_main, ["review-watch", "1", "--repo", "o/r", "--poll-interval", "5"]
    )
    assert result.exit_code == 0
    # All polls after the first should use the same since_ts (epoch-based start,
    # never advanced because session errored each time)
    assert len(since_values) >= 2, "Should have polled at least twice"
    # The cursor must not advance when the session fails
    assert since_values[0] == since_values[1], (
        "Cursor must not advance after a failed session"
    )


def test_review_watch_cursor_overlap_does_not_reprocess_comment(monkeypatch):
    """A comment re-fetched in the cursor's 1s safety-margin overlap must not
    re-trigger a fix session — the dedup guard should skip it as already handled."""
    poll_count = [0]
    spawn_calls = [0]
    # Same comment is returned by every poll to simulate it falling inside the
    # 1-second overlap window created when the cursor is backed off.
    comment = {
        "id": 999,
        "path": "f.py",
        "original_line": 1,
        "body": "needs fix",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": "MEMBER",
    }

    def fake_state(*a):
        poll_count[0] += 1
        if poll_count[0] > 3:
            return _make_pr_state(state="CLOSED")
        return _make_pr_state()

    def fake_spawn(**kw):
        spawn_calls[0] += 1
        return {"exit_reason": "done", "duration_s": 0.1}

    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", fake_state)
    monkeypatch.setattr(
        cmd_review_watch, "get_new_review_comments", lambda *a, **kw: [comment]
    )
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(cmd_review_watch.time, "sleep", lambda s: None)
    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)

    runner = CliRunner()
    result = runner.invoke(
        util_main, ["review-watch", "1", "--repo", "o/r", "--poll-interval", "5"]
    )
    assert result.exit_code == 0
    # The same comment id keeps being "returned" (simulating the overlap
    # window), but a fix session must only spawn for it once.
    assert spawn_calls[0] == 1, (
        "Comment re-fetched inside the cursor overlap must not re-spawn a session"
    )


def test_review_watch_reprocesses_edited_comment(monkeypatch):
    """A comment a reviewer edits *after* it was already processed must be
    treated as new feedback (matching `updated_at`, not just `id`) rather
    than silently dropped forever by the dedup guard."""
    poll_count = [0]
    spawn_calls = [0]
    # Same id, but `updated_at` changes on the second poll to simulate the
    # reviewer editing their comment after the first fix session ran.
    comment_v1 = {
        "id": 999,
        "path": "f.py",
        "original_line": 1,
        "body": "needs fix",
        "user": {"login": "maintainer", "type": "User"},
        "author_association": "MEMBER",
        "updated_at": "2026-08-05T00:00:00Z",
    }
    comment_v2 = {
        **comment_v1,
        "body": "actually needs a different fix",
        "updated_at": "2026-08-05T00:05:00Z",
    }

    def fake_state(*a):
        poll_count[0] += 1
        if poll_count[0] > 3:
            return _make_pr_state(state="CLOSED")
        return _make_pr_state()

    def fake_spawn(**kw):
        spawn_calls[0] += 1
        return {"exit_reason": "done", "duration_s": 0.1}

    def fake_comments(*a, **kw):
        return [comment_v1] if spawn_calls[0] == 0 else [comment_v2]

    monkeypatch.setattr(cmd_review_watch, "_gh_available", lambda: True)
    monkeypatch.setattr(cmd_review_watch, "get_pr_state", fake_state)
    monkeypatch.setattr(cmd_review_watch, "get_new_review_comments", fake_comments)
    monkeypatch.setattr(cmd_review_watch, "get_new_issue_comments", lambda *a, **kw: [])
    monkeypatch.setattr(cmd_review_watch.time, "sleep", lambda s: None)
    monkeypatch.setattr(cmd_review_watch, "spawn_review_session", fake_spawn)

    runner = CliRunner()
    result = runner.invoke(
        util_main, ["review-watch", "1", "--repo", "o/r", "--poll-interval", "5"]
    )
    assert result.exit_code == 0
    # First poll processes comment_v1; once that session is "done", the
    # comment is edited (comment_v2, same id, new updated_at) and must
    # trigger a second fix session rather than being dropped as a duplicate.
    assert spawn_calls[0] == 2, (
        "Editing a comment after it was processed must re-spawn a fix session"
    )
