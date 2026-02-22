import logging
import shlex
import subprocess
from pathlib import Path
from typing import Literal

from ..config import get_config

logger = logging.getLogger(__name__)


TreeMethod = Literal["tree", "git", "ls"]


def get_tree_output(workspace: Path, method: TreeMethod = "git") -> str | None:
    """Get workspace file listing using git, tree, or ls (with automatic fallback).

    Tries methods in order: the requested method first, then falls back to others.
    The default method is 'git' (``git ls-files``) which works in any git repo
    without requiring external tools like ``tree`` to be installed.
    """
    if not get_config().get_env_bool("GPTME_CONTEXT_TREE"):
        return None

    # Check if in a git repository
    in_git_repo = False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=False,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=1,
        )
        in_git_repo = result.returncode == 0
    except Exception as e:
        logger.debug(f"Error checking git repository: {e}")

    # git and tree --gitignore require a git repo; ls works anywhere
    git_methods: dict[TreeMethod, str] = {
        "git": "git ls-files --exclude-standard",
        "tree": "tree -fi --gitignore .",
    }
    non_git_methods: dict[TreeMethod, str] = {
        "ls": "ls -R .",
    }
    methods: dict[TreeMethod, str] = (
        {**git_methods, **non_git_methods} if in_git_repo else dict(non_git_methods)
    )

    # Preferred method order (only include available methods)
    _all_methods: list[TreeMethod] = ["git", "tree", "ls"]
    method_order: list[TreeMethod] = [m for m in _all_methods if m in methods]

    # Start with the requested method (if available), then try others
    if method in methods:
        methods_to_try = [method] + [m for m in method_order if m != method]
    else:
        methods_to_try = method_order

    for current_method in methods_to_try:
        try:
            result = subprocess.run(
                shlex.split(methods[current_method]),
                check=False,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                logger.debug(f"Method {current_method} failed: {result.stderr}")
                continue

            # Check if output is acceptable size
            if len(result.stdout) <= 20000:
                if current_method != method:
                    logger.info(f"Using {current_method} method instead of {method}")
                return result.stdout.strip()
            logger.debug(
                f"Method {current_method} output too long, trying to filter by depth..."
            )
            # Try filtering by depth
            filtered = _reduce_tree_output_by_depth(result.stdout)
            if filtered:
                return filtered

        except subprocess.TimeoutExpired:
            logger.debug(f"Method {current_method} timed out")
            continue
        except Exception as e:
            logger.debug(f"Error with method {current_method}: {e}")
            continue

    logger.warning(
        "All tree methods failed or could not be reduced to acceptable size, skipping."
    )
    return None


def _reduce_tree_output_by_depth(output: str, budget: int = 20000) -> str | None:
    """Reduce tree output by progressively filtering deeper paths until under budget."""
    lines = output.splitlines()
    if not lines:
        return output

    # Find the current maximum depth
    max_depth = max(line.count("/") for line in lines)

    # Try reducing depth from max down to 0
    for depth in range(max_depth, -1, -1):
        filtered_lines = []
        for line in lines:
            if line.count("/") <= depth:
                filtered_lines.append(line)

        filtered_output = "\n".join(filtered_lines)
        if len(filtered_output) <= budget:
            logger.debug(
                f"Filtered tree output to max depth {depth} ({len(filtered_lines)} items)"
            )
            return filtered_output

    # If even depth 0 is too long, return None
    return None
