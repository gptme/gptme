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


def run_full_precommit_checks(
    log, workspace, **kwargs
) -> Generator[Message, None, None]:
    """Hook function that runs full pre-commit checks after message processing.

    Args:
        log: The conversation log
        workspace: Workspace directory path

    Yields:
        Messages with pre-commit check results
    """
    # Check if there are modifications
    from ..chat import check_for_modifications

    if not check_for_modifications(log):
        logger.debug("No modifications, skipping pre-commit checks")
        return

    try:
        # Import here to avoid circular dependency
        from ..util.context import run_precommit_checks

        # Run pre-commit checks on all files
        success, failed_check_message = run_precommit_checks()

        if not success and failed_check_message:
            yield Message("system", failed_check_message, quiet=False)
        elif success:
            yield Message("system", "Pre-commit checks passed", hide=True)

    except Exception as e:
        logger.exception(f"Error running pre-commit checks: {e}")
        yield Message("system", f"Pre-commit check failed: {e}", hide=True)


# Tool specification
tool = ToolSpec(
    name="precommit",
    desc="Automatic pre-commit checks on file saves and after message processing",
    instructions="""
This tool automatically runs pre-commit checks in two scenarios:

1. **Per-file checks (FILE_POST_SAVE)**: After each file is saved
   - Runs pre-commit on the specific saved file
   - Provides immediate feedback on formatting/linting issues

2. **Full checks (MESSAGE_POST_PROCESS)**: After message processing completes
   - Runs pre-commit on all modified files
   - Ensures all changes pass checks before auto-commit

Pre-commit checks include:
- Code formatting (black, prettier, etc.)
- Linting (ruff, eslint, etc.)
- Type checking (mypy, etc.)
- Other configured hooks

The tool will report any failures and suggest fixes.

Enable with: --tools precommit
Or configure pre-commit checks via: GPTME_CHECK=true
""".strip(),
    available=check_precommit_available,
    hooks={
        "precommit_file": (
            HookType.FILE_POST_SAVE.value,
            run_precommit_on_file,
            5,  # Priority: run after other hooks but before commits
        ),
        "precommit_full": (
            HookType.MESSAGE_POST_PROCESS.value,
            run_full_precommit_checks,
            5,  # Priority: run before autocommit (priority 1)
        ),
    },
    disabled_by_default=True,  # Disabled by default, enable with --tools precommit
)

__all__ = ["tool"]
