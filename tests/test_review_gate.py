"""Tests for review-gated delivery enforcement.

Covers:
- Default-branch rejection (cannot push to master/main)
- Feature-branch delivery allowed
- Failure when the review gate is unavailable (no VCS remote)
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from gptme.review_gate import (
    ReviewGateStatus,
    check_delivery_target,
    get_default_branch,
    get_remote_names,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path) -> None:
    env = os.environ.copy()
    env["ALLOW_GIT_IDENTITY"] = "1"
    # Skip host git hooks (identity + master-commit guards) in test repos
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        env=env,
    )


def _make_bare_remote(path: Path, default_branch: str = "master") -> Path:
    """Create a bare git repository that acts as a remote."""
    remote = path / "remote.git"
    remote.mkdir()
    _git(["init", "--bare", f"--initial-branch={default_branch}", str(remote)], path)
    return remote


def _make_repo_with_remote(
    path: Path,
    default_branch: str = "master",
) -> tuple[Path, Path]:
    """Create a local repo with one commit and a bare remote called 'origin'."""
    remote = _make_bare_remote(path, default_branch)
    repo = path / "repo"
    repo.mkdir()
    _git(["init", f"--initial-branch={default_branch}", str(repo)], path)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "init"], repo)
    _git(["remote", "add", "origin", str(remote)], repo)
    _git(["push", "-u", "origin", default_branch], repo)
    return repo, remote


def _make_repo_no_remote(path: Path, default_branch: str = "master") -> Path:
    """Create a local repo with no remote configured."""
    repo = path / "repo_no_remote"
    repo.mkdir()
    _git(["init", f"--initial-branch={default_branch}", str(repo)], path)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


# ── get_remote_names ─────────────────────────────────────────────────────────


def test_get_remote_names_with_origin(tmp_path: Path) -> None:
    repo, _ = _make_repo_with_remote(tmp_path)
    assert get_remote_names(repo) == ["origin"]


def test_get_remote_names_no_remote(tmp_path: Path) -> None:
    repo = _make_repo_no_remote(tmp_path)
    assert get_remote_names(repo) == []


# ── get_default_branch ───────────────────────────────────────────────────────


def test_get_default_branch_master(tmp_path: Path) -> None:
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="master")
    branch = get_default_branch(repo)
    assert branch == "master"


def test_get_default_branch_main(tmp_path: Path) -> None:
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="main")
    branch = get_default_branch(repo)
    assert branch == "main"


def test_get_default_branch_no_remote_returns_none(tmp_path: Path) -> None:
    repo = _make_repo_no_remote(tmp_path)
    branch = get_default_branch(repo)
    assert branch is None


# ── check_delivery_target ────────────────────────────────────────────────────


def test_delivery_target_rejects_default_branch(tmp_path: Path) -> None:
    """Pushing to the default branch must be rejected."""
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="master")
    result = check_delivery_target("master", repo)
    assert not result.ok
    assert result.status == ReviewGateStatus.DEFAULT_BRANCH
    assert "default branch" in result.message.lower()


def test_delivery_target_rejects_main_as_default(tmp_path: Path) -> None:
    """Same rejection applies when the default branch is 'main'."""
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="main")
    result = check_delivery_target("main", repo)
    assert not result.ok
    assert result.status == ReviewGateStatus.DEFAULT_BRANCH


def test_delivery_target_allows_feature_branch(tmp_path: Path) -> None:
    """A non-default feature branch is allowed."""
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="master")
    result = check_delivery_target("feat/my-task", repo)
    assert result.ok
    assert result.status == ReviewGateStatus.OK


def test_delivery_target_allows_fix_branch(tmp_path: Path) -> None:
    """Any non-default name is allowed."""
    repo, _ = _make_repo_with_remote(tmp_path, default_branch="master")
    result = check_delivery_target("fix-3390", repo)
    assert result.ok


def test_delivery_target_fails_when_no_remote(tmp_path: Path) -> None:
    """Without a VCS remote the gate must fail closed."""
    repo = _make_repo_no_remote(tmp_path)
    result = check_delivery_target("feat/my-task", repo)
    assert not result.ok
    assert result.status == ReviewGateStatus.NO_REMOTE
    assert "remote" in result.message.lower()


def test_delivery_target_fails_for_missing_named_remote(tmp_path: Path) -> None:
    """Specifying a remote that does not exist fails closed."""
    repo, _ = _make_repo_with_remote(tmp_path)
    result = check_delivery_target("feat/my-task", repo, remote="upstream")
    assert not result.ok
    assert result.status == ReviewGateStatus.NO_REMOTE


def test_review_gate_result_ok_property(tmp_path: Path) -> None:
    """ReviewGateResult.ok convenience property reflects status."""
    repo, _ = _make_repo_with_remote(tmp_path)
    ok_result = check_delivery_target("feat/my-task", repo)
    bad_result = check_delivery_target("master", repo)
    assert ok_result.ok is True
    assert bad_result.ok is False
