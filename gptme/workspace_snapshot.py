"""Side-git workspace snapshots for opt-in auto-rollback.

Provides cheap pre/post-tool snapshots without touching the user's ``.git``.
Storage is an XDG-located shadow git repo per workspace fingerprint:

    $XDG_STATE_HOME/gptme/workspace-snapshots/<fingerprint>.git

Each snapshot is a commit in the shadow repo; restore is
``git read-tree --reset -u <tree>`` which makes the working tree match the
snapshot exactly (reverts modifications, removes files added since).

This module ports the validated side-git prototype from Bob (see
``bob/scripts/workspace-snapshot.py`` and idea #217) into reusable form.
The hook integration lives in :mod:`gptme.hooks.auto_snapshots`.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .checkpoint import repo_fingerprint
from .dirs import get_state_dir

logger = logging.getLogger(__name__)

SNAPSHOTS_SUBDIR = "workspace-snapshots"
SNAPSHOT_REF = "refs/heads/snapshots"
DEFAULT_MAX_SNAPSHOTS = 50


@dataclass
class Shadow:
    """Shadow git repo bound to a workspace tree."""

    workspace: Path
    git_dir: Path

    @classmethod
    def for_workspace(cls, workspace: Path) -> Shadow:
        wp = Path(workspace).resolve()
        fp = repo_fingerprint(wp)
        git_dir = get_state_dir() / SNAPSHOTS_SUBDIR / f"{fp}.git"
        return cls(workspace=wp, git_dir=git_dir)

    def env(self) -> dict[str, str]:
        e = os.environ.copy()
        e["GIT_DIR"] = str(self.git_dir)
        e["GIT_WORK_TREE"] = str(self.workspace)
        return e

    def run(
        self, *args: str, check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            env=self.env(),
            cwd=self.workspace,
            check=check,
            text=True,
            capture_output=capture,
        )

    def initialized(self) -> bool:
        return (self.git_dir / "HEAD").exists()


_EXCLUDE_RULES = (
    # No leading slash on any rule — matches at any depth in nested monorepos.
    # Leading / would anchor to the shadow-repo root only, missing subdirs.
    ".git/\n"
    ".venv/\n"
    "__pycache__/\n"
    "*.pyc\n"
    "node_modules/\n"
    ".mypy_cache/\n"
    ".ruff_cache/\n"
    ".pytest_cache/\n"
)


def _excludes_for(workspace: Path) -> str:
    """Exclude rules for the shadow repo — keep snapshots cheap."""
    del workspace  # rules are workspace-agnostic for v1
    return _EXCLUDE_RULES


def init_shadow(workspace: Path) -> Shadow:
    """Initialize a shadow snapshot repo for ``workspace``. Idempotent."""
    shadow = Shadow.for_workspace(workspace)
    if shadow.initialized():
        return shadow

    shadow.git_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", "--bare", str(shadow.git_dir)], check=True
    )
    # Operate via env vars, not via the bare-repo working-tree machinery.
    subprocess.run(
        ["git", "--git-dir", str(shadow.git_dir), "config", "core.bare", "false"],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(shadow.git_dir),
            "config",
            "user.email",
            "snapshot@gptme.local",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(shadow.git_dir),
            "config",
            "user.name",
            "gptme-workspace-snapshot",
        ],
        check=True,
    )
    # Distinct branch name so user-installed hooks targeting master/main
    # cannot fire on our internal snapshots.
    subprocess.run(
        [
            "git",
            "--git-dir",
            str(shadow.git_dir),
            "symbolic-ref",
            "HEAD",
            SNAPSHOT_REF,
        ],
        check=True,
    )
    excludes = shadow.git_dir / "info" / "exclude"
    excludes.parent.mkdir(parents=True, exist_ok=True)
    excludes.write_text(_excludes_for(workspace))

    # Take an initial snapshot so `restore` always has a target.
    snapshot(shadow, label="initial")
    return shadow


def snapshot(
    shadow: Shadow,
    label: str = "snapshot",
    stage: bool = True,
    n_msgs: int | None = None,
) -> str | None:
    """Create a snapshot. Returns short SHA, or ``None`` on failure.

    When *n_msgs* is provided it is embedded in the commit message so that
    :func:`get_snapshot_n_msgs` can later reconstruct the conversation size at
    snapshot time for the ``/snapshot diff`` conversation-summary feature.
    """
    if not shadow.initialized():
        return None
    if stage:
        shadow.run("add", "-A")
    commit_msg = label if n_msgs is None else f"{label}\nn_msgs={n_msgs}"
    # Allow empty so consecutive identical snapshots still record a ref.
    # Bypass user hooks: internal bookkeeping, not a social commit.
    result = shadow.run(
        "commit",
        "--allow-empty",
        "--no-verify",
        "--no-gpg-sign",
        "-m",
        commit_msg,
        check=False,
    )
    if result.returncode != 0:
        logger.debug("snapshot commit failed: %s", result.stderr)
        return None
    sha = shadow.run("rev-parse", "--short", "HEAD").stdout.strip()
    return sha


def get_snapshot_n_msgs(shadow: Shadow, sha: str) -> int | None:
    """Return the conversation message count embedded in a snapshot, or ``None``."""
    result = shadow.run("log", "--format=%B", "-1", sha, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines()[1:]:
        if line.startswith("n_msgs="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    return None


def list_snapshots(shadow: Shadow, limit: int = 20) -> list[tuple[str, str]]:
    """Return list of ``(short_sha, label)`` tuples, newest first."""
    if not shadow.initialized():
        return []
    result = shadow.run(
        "log",
        "--pretty=format:%h\t%s",
        "--no-decorate",
        f"-{limit}",
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return []
    out: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\t" in line:
            sha, label = line.split("\t", 1)
            out.append((sha, label))
    return out


def restore(shadow: Shadow, snapshot_id: str) -> bool:
    """Restore the workspace tree from ``snapshot_id``.

    Takes a safety snapshot of current state first so restore is reversible.
    Uses ``read-tree --reset -u`` which makes the working tree exactly match
    the snapshot — reverts modifications, removes files added since.

    HEAD is intentionally NOT moved; snapshots remain a linear audit log.
    """
    if not shadow.initialized():
        return False
    safety_sha = snapshot(shadow, label=f"pre-restore-to-{snapshot_id}")
    if safety_sha is None:
        logger.warning(
            "restore: safety pre-restore snapshot failed; aborting to protect working tree"
        )
        return False
    tree = shadow.run("rev-parse", f"{snapshot_id}^{{tree}}", check=False)
    if tree.returncode != 0:
        logger.warning("restore: cannot resolve tree for %s", snapshot_id)
        return False
    result = shadow.run("read-tree", "--reset", "-u", tree.stdout.strip(), check=False)
    if result.returncode != 0:
        logger.warning("restore read-tree failed: %s", result.stderr)
        return False
    return True


def prune(shadow: Shadow, keep: int = DEFAULT_MAX_SNAPSHOTS) -> int:
    """Keep newest ``keep`` snapshots; drop the rest. Returns dropped count.

    Implementation: collect the ``keep`` newest (tree, message) pairs, replay
    them as a fresh orphan chain, and point SNAPSHOT_REF at the new tip.
    Older commits become unreachable and ``git gc`` can reclaim them.
    """
    if not shadow.initialized() or keep <= 0:
        return 0
    count = shadow.run("rev-list", "--count", SNAPSHOT_REF, check=False)
    if count.returncode != 0:
        return 0
    try:
        total = int(count.stdout.strip())
    except ValueError:
        return 0
    if total <= keep:
        return 0
    to_drop = total - keep
    # Collect (tree, full body) for the ``keep`` newest commits in one pass.
    # Use ASCII control characters as record/field separators so commit bodies
    # can still contain ordinary newlines.
    log_output = shadow.run(
        "log",
        "--format=%H%x1f%T%x1f%B%x1e",
        f"-{keep}",
        SNAPSHOT_REF,
        check=False,
    )
    if log_output.returncode != 0 or not log_output.stdout.strip():
        return 0
    entries: list[tuple[str, str]] = []
    for record in log_output.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue
        _, tree, body = parts
        tree = tree.strip()
        body = body.strip()
        if tree and body:
            entries.append((tree, body))
    if not entries:
        return 0
    # Reverse so we build oldest-of-kept → newest (oldest is entries[-1]).
    entries.reverse()
    # Create an orphan root from the oldest kept commit.
    first_tree, first_msg = entries[0]
    res = shadow.run("commit-tree", first_tree, "-m", first_msg, check=False)
    if res.returncode != 0:
        return 0
    parent = res.stdout.strip()
    # Chain remaining commits onto the orphan root.
    for tree, msg in entries[1:]:
        res = shadow.run("commit-tree", tree, "-p", parent, "-m", msg, check=False)
        if res.returncode != 0:
            return 0
        parent = res.stdout.strip()
    # Point the ref at the new tip.
    reset = shadow.run("update-ref", SNAPSHOT_REF, parent, check=False)
    if reset.returncode != 0:
        return 0
    return to_drop


def prune_by_age(shadow: Shadow, days: int = 30) -> int:
    """Drop snapshots older than ``days`` days. Returns dropped count.

    Always keeps at least one snapshot (the most recent) regardless of age.
    Implementation mirrors :func:`prune`: collect entries to keep, replay as a
    fresh orphan chain, and point SNAPSHOT_REF at the new tip.
    """
    if not shadow.initialized() or days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    total = shadow.run("rev-list", "--count", SNAPSHOT_REF, check=False)
    if total.returncode != 0:
        return 0
    try:
        total_count = int(total.stdout.strip())
    except ValueError:
        return 0
    recent = shadow.run(
        "rev-list",
        "--count",
        f"--since=@{cutoff}",
        SNAPSHOT_REF,
        check=False,
    )
    if recent.returncode != 0:
        return 0
    try:
        keep_count = int(recent.stdout.strip())
    except ValueError:
        return 0
    keep_count = keep_count or 1
    to_drop = total_count - keep_count
    if to_drop <= 0:
        return 0
    # Fetch only the survivors we need to replay.
    log_output = shadow.run(
        "log",
        f"-{keep_count}",
        "--format=%H%x1f%T%x1f%ct%x1f%B%x1e",
        SNAPSHOT_REF,
        check=False,
    )
    if log_output.returncode != 0 or not log_output.stdout.strip():
        return 0
    all_entries: list[tuple[str, str, int]] = []  # (tree, body, timestamp)
    for record in log_output.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x1f", 3)
        if len(parts) != 4:
            continue
        _, tree, ts_str, body = parts
        tree = tree.strip()
        body = body.strip()
        try:
            ts = int(ts_str.strip())
        except ValueError:
            continue
        if tree and body:
            all_entries.append((tree, body, ts))
    if len(all_entries) != keep_count:
        return 0
    # Rebuild orphan chain oldest-first (entries arrive newest-first from log).
    all_entries.reverse()
    first_tree, first_msg, _ = all_entries[0]
    res = shadow.run("commit-tree", first_tree, "-m", first_msg, check=False)
    if res.returncode != 0:
        return 0
    parent = res.stdout.strip()
    for tree, msg, _ in all_entries[1:]:
        res = shadow.run("commit-tree", tree, "-p", parent, "-m", msg, check=False)
        if res.returncode != 0:
            return 0
        parent = res.stdout.strip()
    reset = shadow.run("update-ref", SNAPSHOT_REF, parent, check=False)
    if reset.returncode != 0:
        return 0
    return to_drop


def tree_hash(shadow: Shadow, stage: bool = True) -> str | None:
    """Return the tree hash of the current workspace state (no commit)."""
    if not shadow.initialized():
        return None
    if stage:
        shadow.run("add", "-A")
    res = shadow.run("write-tree", check=False)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None
