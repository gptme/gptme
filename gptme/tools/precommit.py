"""
Pre-commit hook tool that automatically runs pre-commit checks after file saves.
"""

import logging
import subprocess
from collections.abc import Generator
from pathlib import Path

from ..hooks import HookType
from ..message import Message
from .base import ToolSpec

logger = logging.getLogger(__name__)


def run_precommit_on_file(
    path: Path, content: str, created: bool = False
) -> Generator[Message, None, None]:
    """Hook function that runs pre-commit on saved files.

    Args:
        path: Path to the saved file
        content: Content that was saved
        created: Whether the file was newly created

    Yields:
        Messages with pre-commit results
    """
    try:
        # Check if pre-commit is available
        check_result = subprocess.run(
            ["pre-commit", "--version"], capture_output=True, text=True, timeout=5
        )
        if check_result.returncode != 0:
            logger.debug("pre-commit not available, skipping hook")
            return

    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.debug("pre-commit not found or timed out, skipping hook")
        return

    try:
        # Run pre-commit on the specific file
        result = subprocess.run(
            ["pre-commit", "run", "--files", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=path.parent,
        )

        if result.returncode != 0:
            # Pre-commit checks failed
            output = result.stdout or result.stderr
            yield Message(
                "system",
                f"Pre-commit checks failed for {path.name}:\n```\n{output}\n```",
            )
        else:
            # Pre-commit checks passed
            yield Message(
                "system",
                f"Pre-commit checks passed for {path.name}",
                hide=True,  # Hide success messages to reduce noise
            )

    except subprocess.TimeoutExpired:
        yield Message(
            "system", f"Pre-commit checks timed out for {path.name}", hide=True
        )
    except Exception as e:
        logger.exception(f"Error running pre-commit on {path}: {e}")
        yield Message(
            "system", f"Error running pre-commit on {path.name}: {e}", hide=True
        )


def check_precommit_available() -> bool:
    """Check if pre-commit is available."""
    try:
        result = subprocess.run(
            ["pre-commit", "--version"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Tool specification
tool = ToolSpec(
    name="precommit",
    desc="Automatic pre-commit checks on file saves",
    instructions="""
This tool automatically runs pre-commit checks after files are saved.
It hooks into the FILE_POST_SAVE event and runs pre-commit on the saved file.

Pre-commit checks include:
- Code formatting (black, prettier, etc.)
- Linting (ruff, eslint, etc.)
- Type checking (mypy, etc.)
- Other configured hooks

The tool will report any failures and suggest fixes.
""".strip(),
    available=check_precommit_available,
    hooks={
        "precommit_check": (
            HookType.FILE_POST_SAVE.value,
            run_precommit_on_file,
            5,  # Priority: run after other hooks but before commits
        )
    },
    disabled_by_default=True,  # Disabled by default, enable with --tools precommit
)

__all__ = ["tool"]
