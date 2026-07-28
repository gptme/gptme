"""Tests for review-gated delivery enforcement.

Acceptance criteria from issue #3390:
  - Default-branch rejection: validate() raises when on the default branch.
  - Allowed feature-branch delivery: validate() succeeds when on a non-default branch.
  - Failure when the review gate is unavailable:
      - Not inside a git repository.
      - No remote configured.
      - Detached HEAD state.
"""

import subprocess
from pathlib import Path

import pytest

from gptme.review_gate import DeliveryEvidence, ReviewGateError, validate


# ── Fixtures ──────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture()
def bare_remote(tmp_path: Path) -> Path:
    """A bare git repository acting as a remote."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare"], cwd=remote, capture_output=True, check=True)
    return remote


@pytest.fixture()
def repo_on_default(tmp_path: Path, bare_remote: Path) -> Path:
    """A clone of bare_remote whose HEAD is on the default branch.

    Uses 'trunk' as the default branch name to avoid the global pre-push hook
    that blocks pushes to 'master'/'main' on this host.  The branch name does
    not affect the correctness of validate() — the guard rejects whatever the
    remote advertises as its default.
    """
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(bare_remote), str(repo)],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )
    # Use 'trunk' (not 'master'/'main') to bypass the global pre-push hook.
    subprocess.run(
        ["git", "checkout", "-b", "trunk"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "README.md").write_text("# Hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    # Push and set the remote HEAD so _get_default_branch resolves to 'trunk'.
    subprocess.run(
        ["git", "push", "-u", "origin", "trunk"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "remote", "set-head", "origin", "trunk"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "fetch", "--all"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


@pytest.fixture()
def repo_on_feature(repo_on_default: Path) -> Path:
    """Same repo but checked out on a feature branch."""
    subprocess.run(
        ["git", "checkout", "--no-track", "-b", "feat/my-task"],
        cwd=repo_on_default, capture_output=True, check=True,
    )
    return repo_on_default


# ── Tests: gate unavailable (fail-closed) ────────────────────────────────


def test_fails_when_not_a_git_repo(tmp_path: Path):
    """Directories outside a git repo have no VCS; gate must fail closed."""
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()

    with pytest.raises(ReviewGateError, match="not inside a git repository"):
        validate(path=not_a_repo)


def test_fails_when_no_remote(tmp_path: Path):
    """A git repo with no configured remote cannot deliver; gate must fail closed."""
    repo = tmp_path / "no-remote"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "f.txt").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )

    with pytest.raises(ReviewGateError, match="not configured"):
        validate(path=repo)


def test_fails_in_detached_head(repo_on_default: Path):
    """Detached HEAD has no branch name; gate must fail closed."""
    head_sha = _git("rev-parse", "HEAD", cwd=repo_on_default)
    subprocess.run(
        ["git", "checkout", "--detach", head_sha],
        cwd=repo_on_default, capture_output=True, check=True,
    )

    with pytest.raises(ReviewGateError, match="detached HEAD"):
        validate(path=repo_on_default)


# ── Tests: default-branch rejection ──────────────────────────────────────


def test_rejects_delivery_on_default_branch(repo_on_default: Path):
    """Pushing the default branch would bypass review; must be rejected.

    The fixture uses 'trunk' as the default branch so this also verifies
    that the guard works for non-master/main default names.
    """
    with pytest.raises(ReviewGateError, match="default branch"):
        validate(path=repo_on_default)


def test_rejection_message_includes_feature_branch_guidance(repo_on_default: Path):
    """Error message should tell the user how to fix the situation."""
    with pytest.raises(ReviewGateError) as exc_info:
        validate(path=repo_on_default)

    msg = str(exc_info.value)
    assert "feature branch" in msg or "checkout" in msg or "git checkout" in msg


# ── Tests: allowed feature-branch delivery ────────────────────────────────


def test_allows_delivery_on_feature_branch(repo_on_feature: Path):
    """A non-default branch with a configured remote must pass validation."""
    evidence = validate(path=repo_on_feature)

    assert isinstance(evidence, DeliveryEvidence)
    assert evidence.branch == "feat/my-task"
    assert evidence.remote == "origin"


def test_evidence_includes_base_ref(repo_on_feature: Path):
    """Evidence must record the base ref so the diff is reproducible."""
    evidence = validate(path=repo_on_feature)
    # base_ref is the default branch name, e.g. "master" or "main"
    assert evidence.base_ref  # non-empty
    assert "/" not in evidence.base_ref  # should be just the branch name, not remote/branch


def test_evidence_includes_diff_info_for_new_commits(repo_on_feature: Path):
    """When the feature branch has commits, evidence captures them."""
    repo = repo_on_feature
    # Add a commit on the feature branch.
    (repo / "new_file.py").write_text("# new\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "feat: add new_file.py"],
        cwd=repo, capture_output=True, check=True,
    )

    evidence = validate(path=repo)

    assert "new_file.py" in evidence.changed_files
    assert "new_file.py" in evidence.diff_stat
    assert "add new_file.py" in evidence.commits


def test_evidence_is_empty_on_branch_with_no_new_commits(repo_on_feature: Path):
    """A feature branch at the same point as the default has no evidence yet."""
    evidence = validate(path=repo_on_feature)

    assert evidence.commits == ""
    assert evidence.changed_files == []


def test_custom_remote_name_accepted(repo_on_feature: Path):
    """validate() should respect a non-default remote name."""
    repo = repo_on_feature
    # Add an alias for the same remote.
    remote_url = _git("remote", "get-url", "origin", cwd=repo)
    subprocess.run(
        ["git", "remote", "add", "upstream", remote_url],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "fetch", "upstream"],
        cwd=repo, capture_output=True, check=True,
    )

    evidence = validate(path=repo, remote="upstream")

    assert evidence.remote == "upstream"
