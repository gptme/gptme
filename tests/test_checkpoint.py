"""Tests for gptme.checkpoint and the gptme-checkpoint CLI."""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

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
from gptme.commands.base import handle_cmd

# --- Fixtures ---


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Keep Git routing/config and checkpoint ledgers isolated from the host."""
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    state = tmp_path / "xdg-state"
    state.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    return state


def _git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(cwd), text=True).strip()


@pytest.fixture
def clean_repo(tmp_path, monkeypatch):
    """Single-root git repo with one committed file, no dirty state."""
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-01T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-01T00:00:00+00:00")
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


@pytest.mark.parametrize("dirty_kind", ["unstaged", "staged", "untracked"])
@pytest.mark.parametrize("include_dirty", [False, True])
def test_restore_dirty_at_same_head(clean_repo, dirty_kind, include_dirty):
    first, _ = create_checkpoint(clean_repo)
    assert first is not None
    if dirty_kind != "untracked":
        (clean_repo / "README.md").write_text("changed\n")
        if dirty_kind == "staged":
            _git(clean_repo, "add", "README.md")
    scratch = clean_repo / "scratch"
    scratch.mkdir()
    (scratch / "new.txt").write_text("scratch\n")
    # Ignored data is outside git clean -fd's discard contract.
    _git(clean_repo, "config", "core.excludesFile", str(clean_repo / ".git" / "ignore"))
    (clean_repo / ".git" / "ignore").write_text("ignored.txt\n")
    ignored = clean_repo / "ignored.txt"
    ignored.write_text("keep\n")
    before = _git(clean_repo, "status", "--porcelain")

    if include_dirty:
        msg = restore_checkpoint(clean_repo, "1", include_dirty=True)
        assert "Restored to checkpoint" in msg
        assert (clean_repo / "README.md").read_text() == "hello\n"
        assert not scratch.exists()
        assert _git(clean_repo, "status", "--porcelain") == ""
    else:
        with pytest.raises(CheckpointError, match="--include-dirty"):
            restore_checkpoint(clean_repo, "1")
        assert _git(clean_repo, "status", "--porcelain") == before
        assert (scratch / "new.txt").read_text() == "scratch\n"
        assert (clean_repo / "README.md").read_text() == (
            "hello\n" if dirty_kind == "untracked" else "changed\n"
        )
    assert _git(clean_repo, "rev-parse", "HEAD") == first.head_sha
    assert ignored.read_text() == "keep\n"


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


def test_cli_restore_dirty_at_same_head(clean_repo):
    first, _ = create_checkpoint(clean_repo)
    assert first is not None
    (clean_repo / "README.md").write_text("changed\n")
    scratch = clean_repo / "scratch.txt"
    scratch.write_text("scratch\n")
    sha_prefix = first.head_sha[:12]
    # Decimal identifiers select checkpoint indices, not SHA prefixes.
    assert not sha_prefix.isdigit()
    result = CliRunner().invoke(
        checkpoint_main,
        ["restore", sha_prefix, str(clean_repo), "--include-dirty"],
    )
    assert result.exit_code == 0, result.output
    assert "Restored to checkpoint" in result.output
    assert (clean_repo / "README.md").read_text() == "hello\n"
    assert not scratch.exists()
    assert _git(clean_repo, "rev-parse", "HEAD") == first.head_sha


@pytest.mark.parametrize("include_dirty", [False, True])
def test_slash_restore_dirty_at_same_head(clean_repo, capsys, include_dirty):
    first, _ = create_checkpoint(clean_repo)
    assert first is not None
    (clean_repo / "README.md").write_text("changed\n")
    scratch = clean_repo / "scratch.txt"
    scratch.write_text("scratch\n")
    manager = MagicMock()
    manager.workspace = str(clean_repo)
    command = "/checkpoint restore 1" + (" --include-dirty" if include_dirty else "")

    list(handle_cmd(command, manager))

    output = capsys.readouterr().out
    if include_dirty:
        assert "Restored to checkpoint" in output
        assert (clean_repo / "README.md").read_text() == "hello\n"
        assert not scratch.exists()
    else:
        assert "not safe to restore" in output
        assert (clean_repo / "README.md").read_text() == "changed\n"
        assert scratch.read_text() == "scratch\n"
    assert _git(clean_repo, "rev-parse", "HEAD") == first.head_sha


def test_cli_list_empty_repo(clean_repo):
    runner = CliRunner()
    result = runner.invoke(checkpoint_main, ["list", str(clean_repo)])
    assert result.exit_code == 0
    assert "No checkpoints" in result.output


# --- Unknown inspection must never authorize a mutation ---


@pytest.mark.parametrize("query", ["root", "head", "status", "worktree", "submodule"])
@pytest.mark.parametrize("failure", ["nonzero", "missing", "permission", "timeout"])
@pytest.mark.parametrize("include_dirty", [False, True])
def test_failed_inspection_refuses_operations(
    clean_repo, monkeypatch, query, failure, include_dirty
):
    from gptme import checkpoint

    create_checkpoint(clean_repo)
    (clean_repo / "README.md").write_text("later committed bytes\n")
    _git(clean_repo, "commit", "-qam", "later")
    head = _git(clean_repo, "rev-parse", "HEAD")
    (clean_repo / "README.md").write_text("valuable dirty bytes\n")
    (clean_repo / "untracked.txt").write_text("valuable untracked bytes\n")
    index = (clean_repo / ".git" / "index").read_bytes()
    ledger = get_ledger_path(clean_repo)
    ledger_bytes = ledger.read_bytes()
    real_run = subprocess.run
    mutations = []

    def fail_query(command, *args, **kwargs):
        git_args = command[1:]
        kind = git_args[0]
        if kind == "rev-parse":
            kind = "root" if "--show-toplevel" in git_args else "head"
        if kind in {"reset", "clean"}:
            mutations.append(command)
        if kind == query:
            if failure == "missing":
                raise FileNotFoundError("synthetic missing git")
            if failure == "permission":
                raise PermissionError("synthetic denied git")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 30)
            return subprocess.CompletedProcess(
                command, 128, "", "synthetic query error"
            )
        return real_run(command, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.subprocess, "run", fail_query)
        decision = classify(clean_repo)
        assert decision.kind == "inspection_failed"
        assert not decision.safe_to_create() and not decision.safe_to_restore()
        for operation in ("create", "restore"):
            with pytest.raises(CheckpointError, match="inspection"):
                if operation == "create":
                    create_checkpoint(clean_repo, include_dirty=include_dirty)
                else:
                    restore_checkpoint(clean_repo, "1", include_dirty=include_dirty)

    assert mutations == []
    assert _git(clean_repo, "rev-parse", "HEAD") == head
    assert (clean_repo / "README.md").read_text() == "valuable dirty bytes\n"
    assert (clean_repo / "untracked.txt").read_text() == "valuable untracked bytes\n"
    assert (clean_repo / ".git" / "index").read_bytes() == index
    assert ledger.read_bytes() == ledger_bytes


@pytest.mark.parametrize(
    ("query", "output"),
    [
        ("root", ""),
        ("root", "relative/path\n"),
        ("head", ""),
        ("head", "HEAD\n"),
        ("head", "abcd\n"),
        ("head", "g" * 40 + "\n"),
        ("worktree", ""),
        ("worktree", "unexpected output\n"),
    ],
)
def test_invalid_inspection_output_refuses_operations(
    clean_repo, monkeypatch, query, output
):
    from gptme import checkpoint

    create_checkpoint(clean_repo)
    original = get_ledger_path(clean_repo).read_bytes()
    real_run = subprocess.run
    mutations = []

    def invalid_query(command, *args, **kwargs):
        git_args = command[1:]
        kind = git_args[0]
        if kind == "rev-parse":
            kind = "root" if "--show-toplevel" in git_args else "head"
        if kind in {"reset", "clean"}:
            mutations.append(command)
        if kind == query:
            return subprocess.CompletedProcess(command, 0, output, "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(checkpoint.subprocess, "run", invalid_query)
    assert classify(clean_repo).kind == "inspection_failed"
    with pytest.raises(CheckpointError, match="inspection"):
        create_checkpoint(clean_repo, include_dirty=True)
    with pytest.raises(CheckpointError, match="inspection"):
        restore_checkpoint(clean_repo, "1", include_dirty=True)
    assert mutations == []
    assert get_ledger_path(clean_repo).read_bytes() == original


@pytest.mark.parametrize("damage", ["index", "head", "missing_head", "missing_objects"])
def test_unusable_metadata_is_not_a_clean_or_non_git_workspace(clean_repo, damage):
    create_checkpoint(clean_repo)
    if damage == "missing_objects":
        (clean_repo / ".git" / "objects").rename(clean_repo / "saved-objects")
    elif damage == "missing_head":
        (clean_repo / ".git" / "HEAD").unlink()
    else:
        (clean_repo / ".git" / ("HEAD" if damage == "head" else "index")).write_bytes(
            b"broken metadata\n"
        )
    ledger = get_ledger_path(clean_repo).read_bytes()
    assert classify(clean_repo).kind == "inspection_failed"
    with pytest.raises(CheckpointError, match="inspection"):
        create_checkpoint(clean_repo, include_dirty=True)
    with pytest.raises(CheckpointError, match="inspection"):
        restore_checkpoint(clean_repo, "1", include_dirty=True)
    assert get_ledger_path(clean_repo).read_bytes() == ledger
    assert (clean_repo / "README.md").read_text() == "hello\n"


def test_unborn_repository_refuses_checkpoint(tmp_path):
    _git(tmp_path, "init", "-q")
    assert classify(tmp_path).kind == "inspection_failed"
    with pytest.raises(CheckpointError, match="inspection"):
        create_checkpoint(tmp_path)
    assert not get_ledger_path(tmp_path).exists()


@pytest.mark.parametrize("command", ["create", "restore", "list", "diff"])
def test_cli_inspection_failure_has_diagnostic(clean_repo, monkeypatch, command):
    from gptme import checkpoint

    monkeypatch.setattr(checkpoint, "GIT_CMD", str(clean_repo / "missing-git"))
    args = [command]
    if command in {"restore", "diff"}:
        args.append("1")
    result = CliRunner().invoke(checkpoint_main, [*args, str(clean_repo)])
    assert result.exit_code == 1
    assert "inspection" in result.output
    assert "missing-git" in result.output
    assert "Traceback" not in result.output
    assert not get_ledger_path(clean_repo).exists()


def test_multiple_worktrees_still_refused(clean_repo, tmp_path):
    other = tmp_path / "other-worktree"
    _git(clean_repo, "worktree", "add", "--detach", str(other))
    decision = classify(clean_repo)
    assert decision.kind == "multi_root"
    assert decision.worktree_count == 2
    with pytest.raises(CheckpointError, match="multi_root"):
        create_checkpoint(clean_repo, include_dirty=True)
    with pytest.raises(CheckpointError, match="multi_root"):
        restore_checkpoint(clean_repo, "1", include_dirty=True)


def test_submodule_still_refused(clean_repo, tmp_path):
    child = tmp_path / "child"
    _git(tmp_path, "clone", "-q", str(clean_repo), str(child))
    _git(
        clean_repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(child),
        "child",
    )
    decision = classify(clean_repo)
    assert decision.kind == "multi_root"
    assert decision.submodule_count == 1
    with pytest.raises(CheckpointError, match="multi_root"):
        create_checkpoint(clean_repo, include_dirty=True)
    with pytest.raises(CheckpointError, match="multi_root"):
        restore_checkpoint(clean_repo, "1", include_dirty=True)


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_supported_head_formats(tmp_path, object_format):
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    _git(repo, "init", "-q", f"--object-format={object_format}")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(
        repo,
        "-c",
        "core.hooksPath=" + os.devnull,
        "commit",
        "-qm",
        "init",
        "--allow-empty",
    )
    decision = classify(repo)
    assert decision.kind == "clean_git"
    assert decision.repo_root == repo
    record, _ = create_checkpoint(repo)
    assert record is not None
    assert record.head_sha == _git(repo, "rev-parse", "HEAD")


def test_head_must_name_a_commit(clean_repo):
    blob = _git(clean_repo, "rev-parse", "HEAD:README.md")
    (clean_repo / ".git" / "HEAD").write_text(blob + "\n")
    assert classify(clean_repo).kind == "inspection_failed"
    with pytest.raises(CheckpointError, match="inspection"):
        create_checkpoint(clean_repo, include_dirty=True)
    assert not get_ledger_path(clean_repo).exists()


@pytest.mark.skipif(
    os.name == "nt", reason="Win32 normalizes trailing directory spaces"
)
def test_repository_root_preserves_trailing_space(clean_repo):
    renamed = clean_repo.with_name("repo with trailing space ")
    clean_repo.rename(renamed)
    decision = classify(renamed)
    assert decision.kind == "clean_git"
    assert decision.repo_root == renamed
    record, _ = create_checkpoint(renamed)
    assert record is not None
