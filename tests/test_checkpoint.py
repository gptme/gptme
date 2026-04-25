"""Tests for gptme.checkpoint and the gptme-checkpoint CLI."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from click.testing import CliRunner

from gptme.checkpoint import (
    CheckpointError,
    classify,
    create_checkpoint,
    diff_checkpoint,
    get_ledger_path,
    list_checkpoints,
    repo_fingerprint,
    restore_checkpoint,
)
from gptme.cli.checkpoint import main as checkpoint_main

# --- Fixtures ---


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Force ledger to live under a per-test XDG_STATE_HOME."""
    state = tmp_path / "xdg-state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(cwd), text=True).strip()


@pytest.fixture
def clean_repo(tmp_path):
    """Single-root git repo with one committed file, no dirty state."""
    repo = tmp_path / "clean-repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "test-branch")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "core.hooksPath", "/dev/null")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


@pytest.fixture
def dirty_repo(clean_repo):
    """A clean repo plus an uncommitted edit and an untracked file."""
    (clean_repo / "README.md").write_text("hello\nmodified\n")
    (clean_repo / "untracked.txt").write_text("scratch\n")
    return clean_repo


@pytest.fixture
def non_git_dir(tmp_path):
    d = tmp_path / "loose"
    d.mkdir()
    return d


# --- classify ---


def test_classify_clean_git(clean_repo):
    decision = classify(clean_repo)
    assert decision.kind == "clean_git"
    assert decision.repo_root == clean_repo.resolve()
    assert decision.head_sha is not None
    assert decision.dirty_tracked == 0
    assert decision.untracked == 0
    assert decision.safe_to_create()
    assert decision.safe_to_restore()


def test_classify_dirty_git(dirty_repo):
    decision = classify(dirty_repo)
    assert decision.kind == "dirty_git"
    assert decision.dirty_tracked == 1
    assert decision.untracked == 1
    assert not decision.safe_to_create()


def test_classify_non_git(non_git_dir):
    decision = classify(non_git_dir)
    assert decision.kind == "non_git"
    assert decision.repo_root is None
    assert decision.head_sha is None
    assert not decision.safe_to_create()


# --- repo_fingerprint / ledger path ---


def test_repo_fingerprint_stable(clean_repo):
    fp1 = repo_fingerprint(clean_repo)
    fp2 = repo_fingerprint(clean_repo)
    assert fp1 == fp2
    assert len(fp1) == 16
    assert all(c in "0123456789abcdef" for c in fp1)


def test_repo_fingerprint_differs_per_repo(tmp_path, clean_repo):
    other = tmp_path / "other-repo"
    other.mkdir()
    assert repo_fingerprint(clean_repo) != repo_fingerprint(other)


def test_ledger_path_under_xdg_state(clean_repo, isolated_state_dir):
    path = get_ledger_path(clean_repo)
    assert isolated_state_dir in path.parents
    assert path.suffix == ".jsonl"


def test_ledger_path_does_not_dirty_user_repo(clean_repo):
    """The whole point of XDG storage: never write inside the user repo."""
    create_checkpoint(clean_repo)
    assert not (clean_repo / ".gptme").exists()
    decision = classify(clean_repo)
    assert decision.kind == "clean_git"  # still clean after checkpoint


# --- create_checkpoint ---


def test_create_checkpoint_clean(clean_repo):
    rec, sha = create_checkpoint(clean_repo)
    assert rec is not None
    assert sha is None
    assert rec.head_sha == _git(clean_repo, "rev-parse", "HEAD")
    assert rec.workspace == str(clean_repo.resolve())

    ledger = get_ledger_path(clean_repo)
    assert ledger.exists()
    line = ledger.read_text().strip()
    data = json.loads(line)
    assert data["head_sha"] == rec.head_sha


def test_create_checkpoint_idempotent_at_same_head(clean_repo):
    first, _ = create_checkpoint(clean_repo)
    second, existing_sha = create_checkpoint(clean_repo)
    assert first is not None
    assert second is None  # idempotent — no duplicate adjacent records
    assert existing_sha == first.head_sha  # SHA returned instead of re-running classify
    assert len(list_checkpoints(clean_repo)) == 1


def test_create_checkpoint_dirty_refused_without_flag(dirty_repo):
    with pytest.raises(CheckpointError, match="--include-dirty"):
        create_checkpoint(dirty_repo)


def test_create_checkpoint_dirty_with_include_dirty(dirty_repo):
    rec, _ = create_checkpoint(dirty_repo, include_dirty=True)
    assert rec is not None


def test_create_checkpoint_non_git_refused(non_git_dir):
    with pytest.raises(CheckpointError, match="non_git"):
        create_checkpoint(non_git_dir)


def test_create_checkpoint_after_new_commit_appends(clean_repo):
    create_checkpoint(clean_repo)
    (clean_repo / "second.txt").write_text("two\n")
    _git(clean_repo, "add", "second.txt")
    _git(clean_repo, "commit", "-q", "-m", "two")
    second, _ = create_checkpoint(clean_repo)
    assert second is not None
    assert len(list_checkpoints(clean_repo)) == 2


# --- restore_checkpoint ---


def test_restore_clean_repo_returns_to_checkpoint(clean_repo):
    first, _ = create_checkpoint(clean_repo)
    assert first is not None

    (clean_repo / "added.txt").write_text("added\n")
    _git(clean_repo, "add", "added.txt")
    _git(clean_repo, "commit", "-q", "-m", "add")

    msg = restore_checkpoint(clean_repo, "1")
    assert "Restored to checkpoint" in msg
    assert _git(clean_repo, "rev-parse", "HEAD") == first.head_sha
    assert not (clean_repo / "added.txt").exists()


def test_restore_at_same_head_is_noop(clean_repo):
    create_checkpoint(clean_repo)
    msg = restore_checkpoint(clean_repo, "1")
    assert "Already at checkpoint" in msg


def test_restore_dirty_refused_without_flag(clean_repo):
    create_checkpoint(clean_repo)
    (clean_repo / "added.txt").write_text("added\n")
    _git(clean_repo, "add", "added.txt")
    _git(clean_repo, "commit", "-q", "-m", "add")
    (clean_repo / "README.md").write_text("now-dirty\n")

    with pytest.raises(CheckpointError, match="--include-dirty"):
        restore_checkpoint(clean_repo, "1")


def test_restore_unknown_identifier(clean_repo):
    create_checkpoint(clean_repo)
    with pytest.raises(CheckpointError, match="not found|out of range"):
        restore_checkpoint(clean_repo, "99")


def test_restore_include_dirty_removes_untracked(clean_repo):
    """--include-dirty restore must also remove untracked files (git clean -fd)."""
    first, _ = create_checkpoint(clean_repo, include_dirty=True)
    assert first is not None

    # Advance HEAD with a new commit so restore actually moves.
    (clean_repo / "second.txt").write_text("two\n")
    _git(clean_repo, "add", "second.txt")
    _git(clean_repo, "commit", "-q", "-m", "second")

    # Add an untracked file *after* the checkpoint — simulate agent-created scratch.
    (clean_repo / "scratch.txt").write_text("scratch\n")
    assert (clean_repo / "scratch.txt").exists()

    msg = restore_checkpoint(clean_repo, "1", include_dirty=True)
    assert "Restored" in msg
    assert _git(clean_repo, "rev-parse", "HEAD") == first.head_sha
    assert not (clean_repo / "second.txt").exists()
    assert not (clean_repo / "scratch.txt").exists(), (
        "untracked file survived --include-dirty restore (git clean -fd not run)"
    )


def test_resolve_index_above_999(clean_repo):
    """Index resolution must work for indices with 4+ digits."""
    # Create two checkpoints; verify index "10" (4+ digit threshold doesn't apply
    # at small counts, but isdigit() should handle large numbers too).
    create_checkpoint(clean_repo)
    (clean_repo / "b.txt").write_text("b\n")
    _git(clean_repo, "add", "b.txt")
    _git(clean_repo, "commit", "-q", "-m", "b")
    second, _ = create_checkpoint(clean_repo)
    assert second is not None

    # "2" should resolve even though len("2") == 1; confirm "10" raises out-of-range
    # (only 2 records) rather than silently falling through to SHA matching.
    with pytest.raises(CheckpointError, match="out of range"):
        restore_checkpoint(clean_repo, "10")


# --- diff_checkpoint ---


def test_diff_checkpoint_shows_changes(clean_repo):
    create_checkpoint(clean_repo)
    (clean_repo / "new.txt").write_text("new\n")
    _git(clean_repo, "add", "new.txt")
    _git(clean_repo, "commit", "-q", "-m", "add new")

    out = diff_checkpoint(clean_repo, "1")
    assert "new.txt" in out


# --- CLI ---


def test_cli_create_and_list(clean_repo):
    runner = CliRunner()
    result = runner.invoke(checkpoint_main, ["create", str(clean_repo)])
    assert result.exit_code == 0, result.output
    assert "Checkpoint created" in result.output

    result = runner.invoke(checkpoint_main, ["list", str(clean_repo)])
    assert result.exit_code == 0, result.output
    assert "Workspace" in result.output  # header present
    assert str(clean_repo.resolve()) in result.output


def test_cli_create_dirty_refused(dirty_repo):
    runner = CliRunner()
    result = runner.invoke(checkpoint_main, ["create", str(dirty_repo)])
    assert result.exit_code == 1
    assert "include-dirty" in result.output


def test_cli_restore_roundtrip(clean_repo):
    first_head = _git(clean_repo, "rev-parse", "HEAD")
    runner = CliRunner()
    runner.invoke(checkpoint_main, ["create", str(clean_repo)])

    (clean_repo / "extra.txt").write_text("e\n")
    _git(clean_repo, "add", "extra.txt")
    _git(clean_repo, "commit", "-q", "-m", "extra")

    result = runner.invoke(checkpoint_main, ["restore", "1", str(clean_repo)])
    assert result.exit_code == 0, result.output
    assert "Restored" in result.output
    assert _git(clean_repo, "rev-parse", "HEAD") == first_head


def test_cli_list_empty_repo(clean_repo):
    runner = CliRunner()
    result = runner.invoke(checkpoint_main, ["list", str(clean_repo)])
    assert result.exit_code == 0
    assert "No checkpoints" in result.output
