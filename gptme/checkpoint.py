"""Workspace checkpoint primitives for ``gptme checkpoint``.

Lightweight, Git-backed recovery markers for single-root Git workspaces:
record the current ``HEAD``, list previous checkpoints, diff against them,
and restore by ``git reset --hard <head>``.

This is **not** conversation backtracking (see gptme/gptme#523) — it is purely
about recovering filesystem state when an agent run touches more than the user
expected. The implementation is conservative on purpose:

- ``clean_git`` workspaces are checkpointed and restored without ceremony.
- ``dirty_git`` workspaces require explicit ``--include-dirty``.
- ``non_git`` and ``multi_root`` workspaces are refused entirely in this MVP.

Storage lives in ``$XDG_STATE_HOME/gptme/checkpoints/<fingerprint>.jsonl``
(via :func:`gptme.dirs.get_state_dir`) so user repos stay clean — no
``.gptme/`` directory written into the working tree.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .dirs import get_state_dir

BackendKind = Literal["clean_git", "dirty_git", "non_git", "multi_root"]

CHECKPOINTS_SUBDIR = "checkpoints"


class CheckpointError(Exception):
    """Checkpoint operation refused or failed."""


@dataclasses.dataclass(frozen=True)
class BackendDecision:
    """Result of inspecting a path for checkpoint backend applicability."""

    kind: BackendKind
    workspace: Path
    repo_root: Path | None
    head_sha: str | None
    dirty_tracked: int
    untracked: int
    worktree_count: int
    submodule_count: int
    reason: str

    def safe_to_create(self) -> bool:
        return self.kind == "clean_git"

    def safe_to_restore(self) -> bool:
        return self.kind == "clean_git"


@dataclasses.dataclass(frozen=True)
class CheckpointRecord:
    """A single checkpoint entry in the ledger."""

    session_id: str
    timestamp: str  # ISO 8601 with timezone
    head_sha: str
    workspace: str  # resolved absolute path

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)


def _git(repo: Path, *args: str) -> str:
    """Run ``git`` in ``repo``; return stripped stdout, or ``''`` on non-zero."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _find_repo_root(path: Path) -> Path | None:
    if not path.exists():
        return None
    out = _git(path, "rev-parse", "--show-toplevel")
    return Path(out) if out else None


def _count_worktrees(repo: Path) -> int:
    out = _git(repo, "worktree", "list", "--porcelain")
    if not out:
        return 0
    return sum(1 for line in out.splitlines() if line.startswith("worktree "))


def _count_submodules(repo: Path) -> int:
    out = _git(repo, "submodule", "status")
    if not out:
        return 0
    return sum(1 for line in out.splitlines() if line.strip())


def _porcelain_counts(repo: Path) -> tuple[int, int]:
    out = _git(repo, "status", "--porcelain")
    if not out:
        return (0, 0)
    dirty = 0
    untracked = 0
    for line in out.splitlines():
        if not line:
            continue
        code = line[:2]
        if code == "??":
            untracked += 1
        else:
            dirty += 1
    return (dirty, untracked)


def classify(path: str | Path) -> BackendDecision:
    """Classify a workspace path into a checkpoint backend kind.

    Pure inspection: never mutates the workspace. Always returns a populated
    :class:`BackendDecision` so callers can render a clear explanation rather
    than guessing.
    """
    workspace = Path(path).expanduser().resolve()

    repo = _find_repo_root(workspace)
    if repo is None:
        return BackendDecision(
            kind="non_git",
            workspace=workspace,
            repo_root=None,
            head_sha=None,
            dirty_tracked=0,
            untracked=0,
            worktree_count=0,
            submodule_count=0,
            reason="not inside a git repository",
        )

    head = _git(repo, "rev-parse", "HEAD") or None
    dirty_tracked, untracked = _porcelain_counts(repo)
    worktrees = _count_worktrees(repo)
    submodules = _count_submodules(repo)

    if worktrees > 1 or submodules > 0:
        bits = []
        if worktrees > 1:
            bits.append(f"{worktrees} worktrees")
        if submodules > 0:
            bits.append(f"{submodules} submodules")
        return BackendDecision(
            kind="multi_root",
            workspace=workspace,
            repo_root=repo,
            head_sha=head,
            dirty_tracked=dirty_tracked,
            untracked=untracked,
            worktree_count=worktrees,
            submodule_count=submodules,
            reason="multi-root layout: " + ", ".join(bits),
        )

    if dirty_tracked > 0 or untracked > 0:
        return BackendDecision(
            kind="dirty_git",
            workspace=workspace,
            repo_root=repo,
            head_sha=head,
            dirty_tracked=dirty_tracked,
            untracked=untracked,
            worktree_count=worktrees,
            submodule_count=submodules,
            reason=(
                f"dirty repository: {dirty_tracked} tracked changes, "
                f"{untracked} untracked"
            ),
        )

    return BackendDecision(
        kind="clean_git",
        workspace=workspace,
        repo_root=repo,
        head_sha=head,
        dirty_tracked=0,
        untracked=0,
        worktree_count=worktrees,
        submodule_count=submodules,
        reason="clean single-root git repository",
    )


def repo_fingerprint(repo_root: str | Path) -> str:
    """Stable, compact fingerprint for an absolute repo path.

    Uses the realpath bytes so symlink chains map to the same checkpoint
    file. If the repo is later moved, the fingerprint changes — that is
    deliberate; checkpoints are recovery artifacts, not durable history.
    """
    real = os.path.realpath(str(repo_root))
    return hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]


def get_ledger_path(repo_root: str | Path) -> Path:
    """Return the JSONL ledger path for a repo, under XDG state dir."""
    fp = repo_fingerprint(repo_root)
    return get_state_dir() / CHECKPOINTS_SUBDIR / f"{fp}.jsonl"


def _last_line(path: Path) -> str | None:
    if not path.exists():
        return None
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return None
        chunk_size = min(size, 4096)
        f.seek(max(0, size - chunk_size))
        tail = f.read().decode("utf-8", errors="replace")
        lines = tail.strip().splitlines()
        return lines[-1] if lines else None


def create_checkpoint(
    workspace: str | Path,
    *,
    session_id: str | None = None,
    include_dirty: bool = False,
) -> CheckpointRecord | None:
    """Create a checkpoint and append it to the XDG ledger.

    Returns the new :class:`CheckpointRecord`, or ``None`` if the workspace
    is already at the same HEAD as the most recent checkpoint (idempotent).

    Raises:
        CheckpointError: workspace kind is not checkpointable
        OSError: ledger write failed
    """
    decision = classify(workspace)

    if decision.kind == "clean_git":
        pass
    elif decision.kind == "dirty_git" and include_dirty:
        pass
    elif decision.kind == "dirty_git":
        raise CheckpointError(
            f"cannot create checkpoint in {decision.kind} workspace: "
            f"{decision.reason} "
            "(pass --include-dirty to checkpoint uncommitted changes)"
        )
    else:
        raise CheckpointError(
            f"cannot create checkpoint in {decision.kind} workspace: {decision.reason}"
        )

    if decision.repo_root is None or decision.head_sha is None:
        raise CheckpointError("workspace has no repo root or HEAD")

    record = CheckpointRecord(
        session_id=session_id or uuid.uuid4().hex[:12],
        timestamp=datetime.now(timezone.utc).isoformat(),
        head_sha=decision.head_sha,
        workspace=str(decision.workspace.resolve()),
    )

    ledger_path = get_ledger_path(decision.repo_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    if ledger_path.exists():
        try:
            last_line = _last_line(ledger_path)
            if last_line:
                last = json.loads(last_line)
                if last.get("head_sha") == record.head_sha:
                    return None
        except (json.JSONDecodeError, OSError):
            pass

    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(record.to_json() + "\n")

    return record


def list_checkpoints(repo_root: str | Path) -> list[CheckpointRecord]:
    """Read all checkpoint records for a repo. Empty list if no ledger."""
    ledger_path = get_ledger_path(repo_root)
    if not ledger_path.exists():
        return []

    records: list[CheckpointRecord] = []
    with open(ledger_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(
                    CheckpointRecord(
                        session_id=data["session_id"],
                        timestamp=data["timestamp"],
                        head_sha=data["head_sha"],
                        workspace=data["workspace"],
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return records


def _resolve_checkpoint(repo_root: Path, identifier: str) -> CheckpointRecord:
    """Resolve a 1-based index or SHA prefix to a :class:`CheckpointRecord`."""
    records = list_checkpoints(repo_root)
    if not records:
        raise CheckpointError("no checkpoints found for this repo")

    # Pure decimal integers are unambiguously 1-based indices (SHA prefixes are hex).
    if identifier.isdigit():
        idx = int(identifier)
        if 1 <= idx <= len(records):
            return records[idx - 1]
        raise CheckpointError(f"checkpoint index {idx} out of range (1-{len(records)})")

    matches = [r for r in records if r.head_sha.startswith(identifier)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CheckpointError(f"ambiguous checkpoint SHA prefix {identifier!r}")

    raise CheckpointError(
        f"checkpoint {identifier!r} not found "
        f"(use an index 1-{len(records)} or SHA prefix)"
    )


def restore_checkpoint(
    workspace: str | Path,
    identifier: str,
    *,
    include_dirty: bool = False,
) -> str:
    """Restore working tree to a checkpointed HEAD via ``git reset --hard``.

    Refuses ``dirty_git`` without ``include_dirty``; refuses ``non_git`` and
    ``multi_root`` unconditionally. Returns a human-readable confirmation.

    Raises:
        CheckpointError: workspace not safe, checkpoint not found, or git failed
    """
    decision = classify(workspace)

    if decision.kind == "clean_git":
        pass
    elif decision.kind == "dirty_git" and include_dirty:
        pass
    elif decision.kind == "dirty_git":
        raise CheckpointError(
            "workspace has uncommitted changes — it is not safe to restore "
            "a checkpoint without --include-dirty. Commit or stash your "
            "changes first, or pass --include-dirty to discard them."
        )
    else:
        raise CheckpointError(
            f"cannot restore checkpoint in {decision.kind} workspace: {decision.reason}"
        )

    if decision.repo_root is None:
        raise CheckpointError("workspace has no repo root")

    record = _resolve_checkpoint(decision.repo_root, identifier)
    current_sha = decision.head_sha or "?"

    if record.head_sha == current_sha:
        return f"Already at checkpoint {record.head_sha[:12]} — nothing to restore."

    result = subprocess.run(
        ["git", "reset", "--hard", record.head_sha],
        check=False,
        cwd=decision.repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CheckpointError(
            f"git reset --hard failed: {result.stderr.strip() or 'unknown error'}"
        )

    if include_dirty:
        # git reset --hard does not remove untracked files; clean them up so the
        # workspace is truly restored to the checkpointed state.
        clean = subprocess.run(
            ["git", "clean", "-fd"],
            check=False,
            cwd=decision.repo_root,
            capture_output=True,
            text=True,
        )
        if clean.returncode != 0:
            raise CheckpointError(
                f"git clean -fd failed: {clean.stderr.strip() or 'unknown error'}"
            )

    return (
        f"Restored to checkpoint {record.head_sha[:12]} "
        f"(session={record.session_id})\n"
        f"  Reset HEAD from {current_sha[:12]} -> {record.head_sha[:12]}"
    )


def diff_checkpoint(workspace: str | Path, identifier: str) -> str:
    """Return ``git diff`` output between current HEAD and a checkpoint HEAD."""
    decision = classify(workspace)
    if decision.repo_root is None:
        raise CheckpointError(
            f"cannot diff checkpoint in {decision.kind} workspace: {decision.reason}"
        )
    record = _resolve_checkpoint(decision.repo_root, identifier)

    result = subprocess.run(
        ["git", "diff", record.head_sha],
        check=False,
        cwd=decision.repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CheckpointError(
            f"git diff failed: {result.stderr.strip() or 'unknown error'}"
        )
    return result.stdout
