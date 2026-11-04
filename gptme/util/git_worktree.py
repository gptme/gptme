"""Git worktree utilities for subagent isolation.

Creates temporary git worktrees so subagents can work on isolated copies
of the repository without interfering with the parent's working directory.
"""

import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .git_cmd import GIT_CMD, git_inspect_cmd

logger = logging.getLogger(__name__)

# Default base directory for worktrees
DEFAULT_WORKTREE_BASE = Path("/tmp/gptme-worktrees")


def has_changes(worktree_path: Path) -> bool:
    """Return True if the worktree has local changes vs the repository HEAD.

    Checks both uncommitted file modifications and commits that were made
    inside the worktree branch since it was created.

    Args:
        worktree_path: Path to the worktree to inspect.

    Returns:
        True if changed or inspection cannot establish cleanliness. False only
        for a clean worktree with a known branch-creation baseline. This does
        not change cleanup's directory-removal policy or recover dirty files.
    """
    try:
        # 1. Uncommitted changes (staged or unstaged)
        result = subprocess.run(
            [*git_inspect_cmd(), "status", "--porcelain"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0 or result.stdout:
            return True

        # 2. Committed changes: find commits in the worktree branch that are
        # not reachable from the commit the branch was created at (initial
        # entry in the branch's reflog).
        #
        # git reflog show <branch> lists entries newest-first; the last entry
        # is the creation point. If HEAD != that initial SHA, commits exist.
        branch_r = subprocess.run(
            [*git_inspect_cmd(), "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if branch_r.returncode != 0:
            return True

        branch = branch_r.stdout.removesuffix("\n")
        if (
            not branch
            or branch == "HEAD"
            or branch.startswith("-")
            or any(char.isspace() or ord(char) < 32 for char in branch)
        ):
            # Detached, missing or malformed branch: no known creation point.
            return True

        reflog_r = subprocess.run(
            [*git_inspect_cmd(), "reflog", "show", "--format=%H", branch],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if reflog_r.returncode != 0:
            return True
        shas = reflog_r.stdout.splitlines()
        if not shas or any(
            re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", sha) is None for sha in shas
        ):
            return True
        creation_sha = shas[-1]  # oldest reflog entry = creation point
        ahead_r = subprocess.run(
            [*git_inspect_cmd(), "rev-list", "--count", f"{creation_sha}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if ahead_r.returncode != 0:
            return True
        count = ahead_r.stdout.removesuffix("\n")
        if re.fullmatch(r"[0-9]+", count) is None:
            return True
        return count.lstrip("0") != ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # On error, assume changes exist (safe default: don't lose modified work)
        return True


def get_git_root(path: Path | None = None) -> Path | None:
    """Find the git repository root from the given path.

    Args:
        path: Directory to search from. Defaults to cwd.

    Returns:
        Path to git repo root, or None if not in a git repo.
    """
    try:
        result = subprocess.run(
            [*git_inspect_cmd(), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            cwd=path or Path.cwd(),
            timeout=10,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _worktree_branch_marker(worktree_path: Path) -> Path:
    """Locate gptme's ownership marker in the worktree's Git admin directory."""
    result = subprocess.run(
        [*git_inspect_cmd(), "rev-parse", "--absolute-git-dir"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return Path(result.stdout.strip()) / "gptme-created-branch"


def create_worktree(
    repo_path: Path,
    branch_name: str | None = None,
    worktree_base: Path | None = None,
) -> Path:
    """Create a git worktree for isolated subagent execution.

    Args:
        repo_path: Path to the git repository root.
        branch_name: Branch name for the worktree. Auto-generated if None.
        worktree_base: Base directory for worktrees. Uses DEFAULT_WORKTREE_BASE if None.

    Returns:
        Path to the created worktree directory.

    Raises:
        subprocess.CalledProcessError: If git worktree creation fails.
        FileNotFoundError: If git is not available.
    """
    if worktree_base is None:
        worktree_base = DEFAULT_WORKTREE_BASE

    if branch_name is None:
        branch_name = f"subagent-{uuid.uuid4().hex[:8]}"

    worktree_path = worktree_base / branch_name
    worktree_base.mkdir(parents=True, exist_ok=True)

    # Create worktree with a new branch based on HEAD
    subprocess.run(
        [GIT_CMD, "worktree", "add", str(worktree_path), "-b", branch_name],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    try:
        _worktree_branch_marker(worktree_path).write_text(branch_name, encoding="utf-8")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"Could not record worktree branch ownership: {e}")

    logger.info(f"Created git worktree at {worktree_path} (branch: {branch_name})")
    return worktree_path


def cleanup_worktree(
    worktree_path: Path,
    repo_path: Path | None = None,
    keep_branch_if_changed: bool = False,
) -> str | None:
    """Clean up a git worktree and its associated branch.

    Attempts git worktree remove first, falls back to directory removal.
    Attempts to delete the branch recorded when gptme created the worktree,
    since ``git worktree remove`` only removes the working tree, not the branch.

    Args:
        worktree_path: Path to the worktree to remove.
        repo_path: Path to the main repository. If None, attempts to find it.
        keep_branch_if_changed: When True, preserve the branch if the worktree
            has local changes or commits. The working-tree directory is still
            removed; only the branch ref is kept so the caller can inspect or
            merge the changes later.

    Returns:
        The preserved branch name when ``keep_branch_if_changed=True`` and
        the worktree had changes; ``None`` otherwise.
    """
    if not worktree_path.exists():
        logger.debug(f"Worktree already removed: {worktree_path}")
        return None

    # Read ownership before removing the worktree. Neither the directory
    # basename nor the current HEAD identifies the branch gptme created.
    branch_name: str | None = None
    try:
        branch_name = (
            _worktree_branch_marker(worktree_path).read_text(encoding="utf-8").strip()
            or None
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug(
            f"Worktree branch ownership unavailable; leaving branch refs alone: {e}"
        )

    # Try to find repo_path if not given
    if repo_path is None:
        repo_path = get_git_root(worktree_path)

    # Smart cleanup: preserve the branch when the worktree has local changes.
    # The working-tree directory is always removed; only the branch ref is kept.
    preserved_branch: str | None = None
    if branch_name and keep_branch_if_changed and has_changes(worktree_path):
        preserved_branch = branch_name
        logger.info(
            f"Worktree {worktree_path} has changes — preserving branch {branch_name!r} "
            "for inspection/merge. Directory will still be removed."
        )

    # Try git worktree remove (clean approach)
    if repo_path:
        try:
            subprocess.run(
                [GIT_CMD, "worktree", "remove", "--force", str(worktree_path)],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            logger.info(f"Removed git worktree: {worktree_path}")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"git worktree remove failed: {e}")
            # Fallback: remove directory manually
            try:
                shutil.rmtree(worktree_path)
                logger.info(f"Removed worktree directory (fallback): {worktree_path}")
            except OSError as e2:
                logger.warning(f"Failed to remove worktree directory: {e2}")

        if preserved_branch:
            # Keep the branch — skip deletion so changes remain accessible.
            return preserved_branch

        # Delete the branch that was created for this worktree.
        # git worktree remove only removes the working tree, not the branch.
        # Without ownership metadata, leave legacy or external branch refs alone.
        if branch_name is not None:
            try:
                result = subprocess.run(
                    [GIT_CMD, "branch", "-D", "--", branch_name],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                if result.returncode == 0:
                    logger.info(f"Deleted worktree branch: {branch_name}")
                else:
                    logger.debug(
                        f"Could not delete branch {branch_name!r}: {result.stderr.strip()}"
                    )
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.debug(f"Branch deletion skipped for {branch_name!r}: {e}")

        # Prune stale worktree entries
        try:
            subprocess.run(
                [GIT_CMD, "worktree", "prune"],
                check=False,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    else:
        # No repo_path — just remove the directory
        try:
            shutil.rmtree(worktree_path)
            logger.info(f"Removed worktree directory (no repo): {worktree_path}")
        except OSError as e:
            logger.warning(f"Failed to remove worktree directory: {e}")

    return None
