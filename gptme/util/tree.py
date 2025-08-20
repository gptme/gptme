import logging
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from ..config import get_config

logger = logging.getLogger(__name__)


TreeMethod = Literal["tree", "git", "ls"]


def get_tree_output(workspace: Path, method: TreeMethod = "tree") -> str | None:
    """Get the output of `tree --gitignore .` if available."""
    # TODO: don't depend on `tree` command being installed
    # TODO: default to True (get_config().get_env_bool("GPTME_CONTEXT_TREE") is False)
    if not get_config().get_env_bool("GPTME_CONTEXT_TREE"):
        return None

    # Check if tree command is available
    if shutil.which("tree") is None:
        logger.warning(
            "GPTME_CONTEXT_TREE is enabled, but 'tree' command is not available. Install it to use this feature."
        )
        return None

    # Check if in a git repository
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode != 0:
            logger.debug("Not in a git repository, skipping tree output")
            return None
    except Exception as e:
        logger.warning(f"Error checking git repository: {e}")
        return None

    methods: dict[TreeMethod, str] = {
        "git": "git ls-files --exclude-standard",  # use with git ls-files -o --exclude-standard for unstaged files
        "tree": "tree -fi --gitignore .",  # is -fi more effective? probably
        "ls": "ls -R .",
    }

    # Preferred method order
    method_order: list[TreeMethod] = ["git", "tree", "ls"]

    # Start with the requested method, then try others
    if method in method_order:
        methods_to_try = [method] + [m for m in method_order if m != method]
    else:
        methods_to_try = method_order

    for current_method in methods_to_try:
        try:
            result = subprocess.run(
                shlex.split(methods[current_method]),
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
            else:
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
            logger.info(
                f"Filtered tree output to max depth {depth} ({len(filtered_lines)} items)"
            )
            return filtered_output

    # If even depth 0 is too long, return None
    return None
