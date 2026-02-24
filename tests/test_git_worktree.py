"""Tests for git worktree utilities used by subagent isolation."""

import subprocess
from pathlib import Path

import pytest

from gptme.util.git_worktree import cleanup_worktree, create_worktree, get_git_root


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repository for testing."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    # Use a non-master branch to avoid global git hooks blocking master commits
    subprocess.run(
        ["git", "checkout", "-b", "main"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    # Create initial commit (worktree requires at least one commit)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=repo,
        capture_output=True,
        check=True,
    )
    return repo


def test_get_git_root(git_repo: Path):
    """Test finding git root from a subdirectory."""
    subdir = git_repo / "sub" / "dir"
    subdir.mkdir(parents=True)
    root = get_git_root(subdir)
    assert root == git_repo


def test_get_git_root_not_git(tmp_path: Path):
    """Test get_git_root returns None when not in a git repo."""
    non_repo = tmp_path / "not-a-repo"
    non_repo.mkdir()
    assert get_git_root(non_repo) is None


def test_create_worktree(git_repo: Path, tmp_path: Path):
    """Test creating a git worktree."""
    worktree_base = tmp_path / "worktrees"
    wt = create_worktree(
        git_repo, branch_name="test-branch", worktree_base=worktree_base
    )

    assert wt.exists()
    assert (wt / "README.md").exists()
    assert (wt / "README.md").read_text() == "# Test\n"

    # Verify the worktree is listed by git
    result = subprocess.run(
        ["git", "worktree", "list"],
        check=False,
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert "test-branch" in result.stdout

    # Cleanup
    cleanup_worktree(wt, git_repo)


def test_create_worktree_auto_branch(git_repo: Path, tmp_path: Path):
    """Test creating a worktree with auto-generated branch name."""
    worktree_base = tmp_path / "worktrees"
    wt = create_worktree(git_repo, worktree_base=worktree_base)

    assert wt.exists()
    assert (wt / "README.md").exists()

    # Branch name should start with "subagent-"
    result = subprocess.run(
        ["git", "branch", "--list"],
        check=False,
        cwd=wt,
        capture_output=True,
        text=True,
    )
    assert "subagent-" in result.stdout

    cleanup_worktree(wt, git_repo)


def test_cleanup_worktree(git_repo: Path, tmp_path: Path):
    """Test cleaning up a git worktree."""
    worktree_base = tmp_path / "worktrees"
    wt = create_worktree(
        git_repo, branch_name="cleanup-test", worktree_base=worktree_base
    )
    assert wt.exists()

    cleanup_worktree(wt, git_repo)
    assert not wt.exists()

    # Verify worktree is no longer listed
    result = subprocess.run(
        ["git", "worktree", "list"],
        check=False,
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert "cleanup-test" not in result.stdout


def test_cleanup_nonexistent_worktree(tmp_path: Path):
    """Test cleanup of already-removed worktree doesn't error."""
    fake_path = tmp_path / "nonexistent"
    # Should not raise
    cleanup_worktree(fake_path)


def test_worktree_isolation(git_repo: Path, tmp_path: Path):
    """Test that changes in worktree don't affect main repo."""
    worktree_base = tmp_path / "worktrees"
    wt = create_worktree(git_repo, branch_name="isolated", worktree_base=worktree_base)

    # Create a file in the worktree
    (wt / "new_file.txt").write_text("worktree change\n")

    # Main repo should not have the file
    assert not (git_repo / "new_file.txt").exists()

    # Main repo README should still be unchanged
    assert (git_repo / "README.md").read_text() == "# Test\n"

    cleanup_worktree(wt, git_repo)
