"""Tests for git worktree utilities used by subagent isolation."""

import subprocess
from pathlib import Path

import pytest

from gptme.util.git_worktree import (
    cleanup_worktree,
    create_worktree,
    get_git_root,
    has_changes,
)


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
    """Test cleaning up a git worktree removes the directory and the branch."""
    worktree_base = tmp_path / "worktrees"
    wt = create_worktree(
        git_repo, branch_name="cleanup-test", worktree_base=worktree_base
    )
    assert wt.exists()

    # Verify branch exists before cleanup
    result = subprocess.run(
        ["git", "branch", "--list", "cleanup-test"],
        check=False,
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert "cleanup-test" in result.stdout, "Branch should exist before cleanup"

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

    # Verify the branch was deleted (the key fix — git worktree remove alone
    # removes the working tree but leaves the branch behind, causing branch pollution)
    result = subprocess.run(
        ["git", "branch", "--list", "cleanup-test"],
        check=False,
        cwd=git_repo,
        capture_output=True,
        text=True,
    )
    assert "cleanup-test" not in result.stdout, (
        "Branch should be deleted after cleanup_worktree() — "
        "git worktree remove only removes the directory, not the branch"
    )


def test_cleanup_nonexistent_worktree(tmp_path: Path):
    """Test cleanup of already-removed worktree doesn't error."""
    fake_path = tmp_path / "nonexistent"
    # Should not raise
    cleanup_worktree(fake_path)


def test_cleanup_worktree_slash_branch(git_repo: Path, tmp_path: Path) -> None:
    """Cleanup must not delete an unrelated branch matching the directory name."""
    subprocess.run(
        ["git", "branch", "task"], cwd=git_repo, capture_output=True, check=True
    )
    wt = create_worktree(
        git_repo, branch_name="feature/task", worktree_base=tmp_path / "worktrees"
    )

    cleanup_worktree(wt, git_repo)

    assert not wt.exists()
    branches = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "task" in branches, "Unrelated branch must survive cleanup"
    assert "feature/task" not in branches, "Worktree branch should be removed"


def test_cleanup_preserves_full_branch_name(git_repo: Path, tmp_path: Path) -> None:
    """Smart cleanup returns the full name of a preserved worktree branch."""
    wt = create_worktree(
        git_repo, branch_name="feature/task", worktree_base=tmp_path / "worktrees"
    )
    (wt / "README.md").write_text("changed\n")

    preserved = cleanup_worktree(wt, git_repo, keep_branch_if_changed=True)

    assert preserved == "feature/task"
    assert not wt.exists()


def test_cleanup_does_not_delete_switched_branch(
    git_repo: Path, tmp_path: Path
) -> None:
    """Switching HEAD must not transfer branch ownership to gptme."""
    subprocess.run(
        ["git", "branch", "user-saved"], cwd=git_repo, capture_output=True, check=True
    )
    wt = create_worktree(
        git_repo, branch_name="subagent-owned", worktree_base=tmp_path / "worktrees"
    )
    subprocess.run(
        ["git", "checkout", "user-saved"], cwd=wt, capture_output=True, check=True
    )

    cleanup_worktree(wt, git_repo)

    branches = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "user-saved" in branches
    assert "subagent-owned" not in branches


def test_cleanup_without_ownership_retains_branch(
    git_repo: Path, tmp_path: Path
) -> None:
    """Legacy and external worktrees must not have their branch names guessed."""
    wt = tmp_path / "external"
    subprocess.run(
        ["git", "worktree", "add", str(wt), "-b", "external"],
        cwd=git_repo,
        capture_output=True,
        check=True,
    )

    cleanup_worktree(wt, git_repo)

    assert not wt.exists()
    result = subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/external"],
        cwd=git_repo,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


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


# ---------------------------------------------------------------------------
# Tests for has_changes() and smart cleanup (keep_branch_if_changed)
# ---------------------------------------------------------------------------


def test_has_changes_clean_worktree(git_repo: Path, tmp_path: Path):
    """has_changes() returns False for a fresh worktree with no modifications."""
    wt = create_worktree(
        git_repo,
        branch_name="clean-wt",
        worktree_base=tmp_path / "worktrees",
    )
    try:
        assert not has_changes(wt), "Fresh worktree should have no changes"
    finally:
        cleanup_worktree(wt, git_repo)


def test_has_changes_uncommitted_file(git_repo: Path, tmp_path: Path):
    """has_changes() returns True when there are uncommitted file modifications."""
    wt = create_worktree(
        git_repo,
        branch_name="dirty-wt",
        worktree_base=tmp_path / "worktrees",
    )
    try:
        (wt / "new_file.txt").write_text("change\n")
        assert has_changes(wt), "Worktree with new file should show changes"
    finally:
        cleanup_worktree(wt, git_repo)


def test_has_changes_committed(git_repo: Path, tmp_path: Path):
    """has_changes() returns True for a committed change on the worktree branch."""
    wt = create_worktree(
        git_repo,
        branch_name="committed-wt",
        worktree_base=tmp_path / "worktrees",
    )
    try:
        (wt / "new_file.txt").write_text("change\n")
        subprocess.run(["git", "add", "."], cwd=wt, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "--no-verify", "-m", "worktree commit"],
            cwd=wt,
            capture_output=True,
            check=True,
        )
        # The worktree has a commit not in the main repo
        assert has_changes(wt), "Worktree with local commit should show changes"
    finally:
        cleanup_worktree(wt, git_repo)


def test_cleanup_keeps_branch_when_changed(git_repo: Path, tmp_path: Path):
    """cleanup_worktree keep_branch_if_changed=True preserves branch when worktree has changes."""
    branch = "changed-wt"
    wt = create_worktree(
        git_repo,
        branch_name=branch,
        worktree_base=tmp_path / "worktrees",
    )

    # Add an uncommitted file to make the worktree dirty
    (wt / "agent_output.txt").write_text("result\n")

    preserved = cleanup_worktree(wt, git_repo, keep_branch_if_changed=True)

    # Working tree should be gone
    assert not wt.exists(), "Worktree directory should be removed"
    # Branch should be preserved and returned
    assert preserved == branch

    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert branch in result.stdout, "Branch should be preserved when there are changes"

    # Manual cleanup of the preserved branch
    subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=git_repo,
        capture_output=True,
        check=False,
    )


def test_cleanup_removes_branch_when_unchanged(git_repo: Path, tmp_path: Path):
    """cleanup_worktree keep_branch_if_changed=True removes branch when worktree is clean."""
    branch = "clean-keep"
    wt = create_worktree(
        git_repo,
        branch_name=branch,
        worktree_base=tmp_path / "worktrees",
    )

    preserved = cleanup_worktree(wt, git_repo, keep_branch_if_changed=True)

    # No changes → full cleanup
    assert preserved is None
    assert not wt.exists()

    result = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert branch not in result.stdout, (
        "Branch should be deleted when worktree is clean"
    )


def test_two_worktrees_are_isolated(git_repo: Path, tmp_path: Path):
    """Changes in one worktree do not appear in another worktree."""
    base = tmp_path / "worktrees"
    wt_a = create_worktree(git_repo, branch_name="agent-a", worktree_base=base)
    wt_b = create_worktree(git_repo, branch_name="agent-b", worktree_base=base)

    try:
        (wt_a / "a_output.txt").write_text("from A\n")
        (wt_b / "b_output.txt").write_text("from B\n")

        assert not (wt_a / "b_output.txt").exists(), "A should not see B's files"
        assert not (wt_b / "a_output.txt").exists(), "B should not see A's files"
        assert not (git_repo / "a_output.txt").exists(), "main repo unaffected by A"
        assert not (git_repo / "b_output.txt").exists(), "main repo unaffected by B"
    finally:
        cleanup_worktree(wt_a, git_repo)
        cleanup_worktree(wt_b, git_repo)
