"""Tests for gptme.dataset.trajectory_to_env.

These tests are fully offline: they create temporary git repos and
fake conversation logs rather than touching the real gptme logs directory.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from gptme.dataset.trajectory_to_env import (
    TaskEnvironment,
    _categorise_files,
    _extract_tool_call_counts,
    _find_session_commits,
    _get_entry_commit,
    _get_files_changed,
    _get_session_duration,
    _get_session_model,
    _get_task_description,
    extract_environments,
    scan_sessions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Allow git identity override so temp-repo commits don't trip the identity guard hook.
_GIT_ENV = {**os.environ, "ALLOW_GIT_IDENTITY": "1"}


def _git(args: list[str], cwd: Path, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=_GIT_ENV,
        **kwargs,
    )


def make_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit.

    Uses a non-master/main branch name so the global pre-commit hook that
    guards against direct master commits in external repos doesn't fire.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    # Use a non-protected branch so the guard hook doesn't block test commits.
    _git(["init", "-b", "test-main"], cwd=repo)
    _git(["config", "user.email", "test@test.invalid"], cwd=repo)
    _git(["config", "user.name", "Test"], cwd=repo)
    _git(["config", "commit.gpgsign", "false"], cwd=repo)
    (repo / "README.md").write_text("# Test\n")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    return repo


def add_commit(repo: Path, filename: str, content: str, message: str) -> str:
    """Add a file and commit it; return the SHA."""
    (repo / filename).write_text(content)
    _git(["add", filename], cwd=repo)
    _git(["commit", "-m", message], cwd=repo)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    )
    return result.stdout.strip()


def make_logs_dir(tmp_path: Path, session_id: str, messages: list[dict]) -> Path:
    """Create a fake gptme logs directory with one conversation."""
    logs_dir = tmp_path / "logs"
    conv_dir = logs_dir / f"2026-01-01_topic_{session_id}"
    conv_dir.mkdir(parents=True)
    jsonl = conv_dir / "conversation.jsonl"
    with jsonl.open("w") as fh:
        for msg in messages:
            fh.write(json.dumps(msg) + "\n")
    return logs_dir


def make_messages(session_id: str) -> list[dict]:
    """Return a minimal conversation log referencing *session_id*."""
    return [
        {
            "role": "system",
            "content": "You are a helpful assistant.",
            "timestamp": "2026-01-01T10:00:00Z",
        },
        {
            "role": "user",
            "content": "Fix the broken test for utils.py",
            "timestamp": "2026-01-01T10:00:01Z",
        },
        {
            "role": "assistant",
            "content": "```shell\npython -m pytest tests/\n```",
            "timestamp": "2026-01-01T10:01:00Z",
            "metadata": {"model": "claude-sonnet-4-6"},
        },
    ]


# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


def test_categorise_files_code():
    assert _categorise_files(["src/foo.py", "tests/test_foo.py"]) == "code"


def test_categorise_files_docs():
    assert _categorise_files(["docs/guide.md"]) == "docs"


def test_categorise_files_journal():
    assert _categorise_files(["journal/2026-01-01/notes.md"]) == "journal"


def test_categorise_files_other():
    assert _categorise_files([".gitignore"]) == "other"


def test_categorise_files_empty():
    assert _categorise_files([]) == "other"


def test_extract_tool_call_counts_markdown():
    messages = [
        {"role": "assistant", "content": "```shell\nls\n```\n```python\nprint()\n```"}
    ]
    counts = _extract_tool_call_counts(messages)
    assert counts["shell"] == 1
    assert counts["python"] == 1


def test_extract_tool_call_counts_tool_use_blocks():
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "Bash"},
                {"type": "tool_use", "name": "Read"},
                {"type": "tool_use", "name": "Bash"},
            ],
        }
    ]
    counts = _extract_tool_call_counts(messages)
    assert counts["Bash"] == 2
    assert counts["Read"] == 1


def test_get_task_description_string():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Fix the bug in utils.py"},
    ]
    desc = _get_task_description(messages)
    assert desc == "Fix the bug in utils.py"


def test_get_task_description_list_content():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Part A"},
                {"type": "text", "text": "Part B"},
            ],
        }
    ]
    desc = _get_task_description(messages)
    assert "Part A" in desc
    assert "Part B" in desc


def test_get_task_description_truncates():
    messages = [{"role": "user", "content": "x" * 5000}]
    desc = _get_task_description(messages)
    assert len(desc) <= 2000


def test_get_session_model():
    messages = [
        {"role": "assistant", "content": "...", "metadata": {"model": "claude-foo"}},
    ]
    assert _get_session_model(messages) == "claude-foo"


def test_get_session_model_missing():
    messages = [{"role": "assistant", "content": "..."}]
    assert _get_session_model(messages) == ""


def test_get_session_duration():
    messages = [
        {"role": "user", "content": "q", "timestamp": "2026-01-01T10:00:00Z"},
        {"role": "assistant", "content": "a", "timestamp": "2026-01-01T10:14:07Z"},
    ]
    secs = _get_session_duration(messages)
    assert abs(secs - 847.0) < 1.0


def test_get_session_duration_single():
    messages = [{"role": "user", "content": "q", "timestamp": "2026-01-01T10:00:00Z"}]
    assert _get_session_duration(messages) == 0.0


# ---------------------------------------------------------------------------
# Integration tests — real git repo
# ---------------------------------------------------------------------------


def test_find_session_commits(tmp_path):
    repo = make_repo(tmp_path)
    sha = add_commit(repo, "foo.py", "pass\n", "fix: handle edge case (a1b2)")
    commits = _find_session_commits("a1b2", repo)
    assert sha in commits


def test_find_session_commits_no_match(tmp_path):
    repo = make_repo(tmp_path)
    add_commit(repo, "bar.py", "pass\n", "fix: unrelated change")
    commits = _find_session_commits("zzzz", repo)
    assert commits == []


def test_get_entry_commit(tmp_path):
    repo = make_repo(tmp_path)
    # initial commit is the parent we expect
    init_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=_GIT_ENV,
    ).stdout.strip()
    session_sha = add_commit(repo, "foo.py", "pass\n", "fix: something (abcd)")
    entry = _get_entry_commit(session_sha, repo)
    assert entry == init_sha


def test_get_files_changed(tmp_path):
    repo = make_repo(tmp_path)
    sha = add_commit(repo, "utils.py", "# util\n", "feat: add utils (beef)")
    files = _get_files_changed([sha], repo)
    assert "utils.py" in files


def test_extract_environments_end_to_end(tmp_path):
    session_id = "cafe"
    repo = make_repo(tmp_path)
    add_commit(repo, "script.py", "print('hi')\n", f"feat: add script ({session_id})")

    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    envs = list(extract_environments(repo_path=repo, logs_dir=logs_dir, limit=10))
    assert len(envs) == 1

    env = envs[0]
    assert env.session_id == session_id
    assert env.entry_commit  # non-empty
    assert "script.py" in env.files_changed
    assert env.task_description == "Fix the broken test for utils.py"
    assert env.outcome == "productive"
    assert env.model == "claude-sonnet-4-6"
    assert env.category == "code"


def test_extract_environments_no_commits(tmp_path):
    """Sessions with no associated commits must be skipped."""
    session_id = "dead"
    repo = make_repo(tmp_path)
    # Commit exists but does not reference this session_id
    add_commit(repo, "x.py", "", "chore: unrelated")

    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    envs = list(extract_environments(repo_path=repo, logs_dir=logs_dir))
    assert envs == []


def test_extract_environments_requires_git_repo(tmp_path):
    logs_dir = make_logs_dir(tmp_path, "1234", make_messages("1234"))
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    with pytest.raises(ValueError, match="not a git repository"):
        list(extract_environments(repo_path=not_a_repo, logs_dir=logs_dir))


def test_task_environment_to_jsonl_roundtrip():
    env = TaskEnvironment(
        session_id="cafe",
        entry_commit="abc123",
        solution_commits=["def456"],
        files_changed=["foo.py"],
        task_description="Fix the bug",
        category="code",
        outcome="productive",
        tool_call_counts={"shell": 3},
        model="claude-sonnet-4-6",
        duration_seconds=120.0,
    )
    jsonl = env.to_jsonl()
    d = json.loads(jsonl)
    assert d["session_id"] == "cafe"
    assert d["entry_commit"] == "abc123"
    assert d["category"] == "code"
    env2 = TaskEnvironment.from_dict(d)
    assert env2 == env


def test_scan_sessions_excludes_test_convs(tmp_path):
    """Conversations with IDs like 'tmp*' or 'test-*' are excluded by default."""
    logs_dir = tmp_path / "logs"
    for conv_id in ["test-abc", "tmp123", "2026-01-01_normal_ab12"]:
        (logs_dir / conv_id).mkdir(parents=True)
        (logs_dir / conv_id / "conversation.jsonl").write_text(
            json.dumps({"role": "user", "content": "hi"}) + "\n"
        )

    sessions = list(scan_sessions(logs_dir=logs_dir))
    ids = [s for s, _ in sessions]
    assert not any(i.startswith(("test-", "tmp")) for i in ids)
    assert any("normal" in i for i in ids)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_stats_no_repo(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    runner = CliRunner()
    not_a_repo = tmp_path / "notrepo"
    not_a_repo.mkdir()
    result = runner.invoke(dataset, ["stats", "--repo", str(not_a_repo)])
    assert result.exit_code != 0
    assert "Error" in result.output


def test_cli_export_stdout(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    session_id = "feed"
    repo = make_repo(tmp_path)
    add_commit(repo, "impl.py", "# impl\n", f"feat: implement ({session_id})")
    messages = make_messages(session_id)
    logs_dir = make_logs_dir(tmp_path, session_id, messages)

    runner = CliRunner()
    result = runner.invoke(
        dataset,
        ["export", "--repo", str(repo), "--logs-dir", str(logs_dir)],
    )
    assert result.exit_code == 0, result.output
    # At least one JSONL line on stdout
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("{")]
    assert lines, f"Expected JSON output, got:\n{result.output}"
    env_dict = json.loads(lines[0])
    assert env_dict["session_id"] == session_id


def test_cli_stats_json(tmp_path):
    from gptme.cli.cmd_dataset import dataset

    repo = make_repo(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        dataset, ["stats", "--repo", str(repo), "--json", "--limit", "0"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "scanned" in data
    assert "yield_pct" in data
