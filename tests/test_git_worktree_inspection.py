"""Unknown worktree inspection must never be evidence for deleting a branch."""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.util.git_cmd import GIT_CMD
from gptme.util.git_worktree import create_worktree, has_changes


@pytest.mark.parametrize("command", ["status", "rev-parse", "reflog", "rev-list"])
@pytest.mark.parametrize("failure", ["exit", "timeout", "missing", "permission"])
def test_failed_inspection_is_unknown(
    command: str, failure: str, tmp_path: Path
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command in args:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 10)
            if failure == "missing":
                raise FileNotFoundError("fixture git unavailable")
            if failure == "permission":
                raise PermissionError("fixture inaccessible")
            return subprocess.CompletedProcess(args, 128, "", "fixture failure")
        output = ""
        if "rev-parse" in args:
            output = "fixture\n"
        elif "reflog" in args:
            output = "a" * 40 + "\n"
        elif "rev-list" in args:
            output = "0\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    with patch("gptme.util.git_worktree.subprocess.run", side_effect=run):
        assert has_changes(tmp_path)


@pytest.mark.parametrize(
    ("command", "output"),
    [
        ("status", " \n"),
        ("rev-parse", ""),
        ("rev-parse", "HEAD\n"),
        ("rev-parse", "fixture\nsecond\n"),
        ("rev-parse", "--all\n"),
        ("reflog", ""),
        ("reflog", "broken\n"),
        ("reflog", "a" * 40 + "\nnot-a-sha\n"),
        ("rev-list", ""),
        ("rev-list", "garbage\n"),
        ("rev-list", "-1\n"),
        ("rev-list", "0\n1\n"),
        ("rev-list", "٠\n"),
    ],
)
def test_malformed_or_unsupported_inspection_is_unknown(
    command: str, output: str, tmp_path: Path
) -> None:
    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        values = {
            "status": "",
            "rev-parse": "fixture\n",
            "reflog": "a" * 40 + "\n",
            "rev-list": "0\n",
        }
        current = next(name for name in values if name in args)
        return subprocess.CompletedProcess(
            args, 0, output if current == command else values[current], ""
        )

    with patch("gptme.util.git_worktree.subprocess.run", side_effect=run):
        assert has_changes(tmp_path)


def test_programming_errors_are_not_hidden(tmp_path: Path) -> None:
    with (
        patch(
            "gptme.util.git_worktree.subprocess.run",
            side_effect=TypeError("fixture bug"),
        ),
        pytest.raises(TypeError, match="fixture bug"),
    ):
        has_changes(tmp_path)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [GIT_CMD, "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Synthetic repositories use their own hooks/config, never the operator's.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "fixture")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    (path / "tracked").write_text("baseline")
    git(path, "add", "tracked")
    git(path, "commit", "-qm", "test: baseline")
    return path


def test_real_worktree_states(repo: Path, tmp_path: Path) -> None:
    path = create_worktree(
        repo, branch_name="fixture-child", worktree_base=tmp_path / "trees"
    )
    assert not has_changes(path)
    (path / "tracked").write_text("modified")
    assert has_changes(path)
    git(path, "add", "tracked")
    assert has_changes(path)
    git(path, "commit", "-qm", "test: modified")
    assert has_changes(path)
    git(path, "switch", "-qc", "fresh")
    assert not has_changes(path)
    (path / "untracked").write_text("new")
    assert has_changes(path)
    (path / "untracked").unlink()
    git(path, "checkout", "--detach")
    assert has_changes(path), "detached history has no known branch baseline"


@pytest.mark.skipif(os.name == "nt", reason="fixture uses a POSIX executable hook")
def test_inspection_suppresses_fsmonitor_but_trusted_commits_keep_hooks(
    repo: Path,
) -> None:
    admin = repo / ".git"
    marker = admin / "fsmonitor-fired"
    hook = admin / "fsmonitor-fixture"
    hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    hook.chmod(0o700)
    git(repo, "config", "core.fsmonitor", str(hook))
    assert not has_changes(repo)
    assert not marker.exists()
    committed = admin / "commit-fired"
    commit_hook = admin / "hooks" / "pre-commit"
    commit_hook.write_text(f'#!/bin/sh\ntouch "{committed}"\n')
    commit_hook.chmod(0o700)
    git(repo, "commit", "--allow-empty", "-qm", "test: trusted hook")
    assert committed.exists()
