"""Git worktree utilities for subagent isolation.

Creates temporary git worktrees so subagents can work on isolated copies
of the repository without interfering with the parent's working directory.
"""

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Default base directory for worktrees
DEFAULT_WORKTREE_BASE = Path("/tmp/gptme-worktrees")


def get_git_root(path: Path | None = None) -> Path | None:
    """Find the git repository root from the given path.

    Args:
        path: Directory to search from. Defaults to cwd.

    Returns:
        Path to git repo root, or None if not in a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
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
        ["git", "worktree", "add", str(worktree_path), "-b", branch_name],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )

    logger.info(f"Created git worktree at {worktree_path} (branch: {branch_name})")
    return worktree_path


def cleanup_worktree(
    worktree_path: Path,
    repo_path: Path | None = None,
) -> None:
    """Clean up a git worktree.

    Attempts git worktree remove first, falls back to directory removal.

    Args:
        worktree_path: Path to the worktree to remove.
        repo_path: Path to the main repository. If None, attempts to find it.
    """
    if not worktree_path.exists():
        logger.debug(f"Worktree already removed: {worktree_path}")
        return

    # Try to find repo_path if not given
    if repo_path is None:
        repo_path = get_git_root(worktree_path)

    # Try git worktree remove (clean approach)
    if repo_path:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_path)],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            logger.info(f"Removed git worktree: {worktree_path}")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning(f"git worktree remove failed: {e}")

    # Fallback: remove directory manually
    try:
        shutil.rmtree(worktree_path)
        logger.info(f"Removed worktree directory (fallback): {worktree_path}")
    except OSError as e:
        logger.warning(f"Failed to remove worktree directory: {e}")

    # Also try to prune stale worktree entries
    if repo_path:
        try:
            subprocess.run(
                ["git", "worktree", "prune"],
                check=False,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
