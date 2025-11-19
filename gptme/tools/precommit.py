"""
Pre-commit hook tool that automatically runs pre-commit checks after file saves.

This tool automatically runs pre-commit checks in two scenarios:

1. **Per-file checks (FILE_POST_SAVE)**: After each file is saved
   - Runs pre-commit on the specific saved file
   - Provides immediate feedback on formatting/linting issues

2. **Full checks (MESSAGE_POST_PROCESS)**: After message processing completes
   - Runs pre-commit on all modified files
   - Ensures all changes pass checks before auto-commit

Commands:
- /pre-commit: Manually run pre-commit checks

Pre-commit checks include:
- Code formatting (black, prettier, etc.)
- Linting (ruff, eslint, etc.)
- Type checking (mypy, etc.)
- Other configured hooks

The tool will report any failures and suggest fixes.

Enable with: --tools precommit
Or configure pre-commit checks via: GPTME_CHECK=true
"""

import logging
import shutil
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..commands import CommandContext
from ..config import get_config
from ..hooks import HookType, StopPropagation
from ..logmanager import check_for_modifications
from ..message import Message
from ..util.context import md_codeblock
from .base import ToolSpec

if TYPE_CHECKING:
    from ..logmanager import Log, LogManager

logger = logging.getLogger(__name__)


def use_checks() -> bool:
    """Check if pre-commit checks are enabled.

    Pre-commit checks are enabled when either:
    1. GPTME_CHECK=true is set explicitly, or
    2. A .pre-commit-config.yaml file exists in any parent directory

    Any issues found are included in the context, helping catch and fix code quality
    issues before the user continues the conversation.
    """
    flag: str = get_config().get_env("GPTME_CHECK", "")  # type: ignore
    explicit_enabled = flag.lower() in ("1", "true", "yes")
    explicit_disabled = flag.lower() in ("0", "false", "no")
    if explicit_disabled:
        return False

    # Check for .pre-commit-config.yaml in any parent directory
    has_config = any(
        parent.joinpath(".pre-commit-config.yaml").exists()
        for parent in [Path.cwd(), *Path.cwd().parents]
    )

    if explicit_enabled and not has_config:
        logger.warning(
            "GPTME_CHECK is enabled but no .pre-commit-config.yaml found in any parent directory"
        )

    enabled = explicit_enabled or has_config

    # Check for pre-commit availability
    if enabled and not shutil.which("pre-commit"):
        logger.warning("pre-commit not found, disabling pre-commit checks")
        return False

    return enabled


def run_checks_per_file() -> bool:
    """
    Whether to support running pre-commit checks on each modified file immediately after save.
    Not always a good idea for multi-step/multi-file changes, so disabled by default.
    """
    # TODO: also support checking only modified files in the full run after step complete?
    flag: str = get_config().get_env("GPTME_CHECK_PER_FILE", "false")  # type: ignore
    return flag.lower() in (
        "1",
        "true",
        "yes",
    )


def run_precommit_checks() -> tuple[bool, str | None]:
    """Run pre-commit checks on modified files and return output if there are issues.

    Pre-commit checks will run if either:
    1. GPTME_CHECK=true is set explicitly, or
    2. A .pre-commit-config.yaml file exists in any parent directory

    Returns:
        A tuple (True, None) if no issues found,
        or (False, output) if issues found,
        or (False, None) if interrupted.
        If pre-commit checks are not enabled, returns (False, None).
    """
    if not use_checks():
        logger.debug("Pre-commit checks not enabled")
        return False, None

    # cmd = "pre-commit run --files $(git ls-files -m)"
    cmd = "pre-commit run --all-files"
    start_time = time.monotonic()
    logger.info(f"Running pre-commit checks: {cmd}")
    try:
        subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        return True, None  # No issues found
    except subprocess.CalledProcessError as e:
        # if exit code is 130, it means the user interrupted the process
        if e.returncode == 130:
            raise KeyboardInterrupt() from None
        # If no pre-commit config found
        # Can happen in nested git repos, since we check parent dirs but pre-commit only checks the current repo.
        if ".pre-commit-config.yaml is not a file" in e.stdout:
            return False, None

        logger.error(f"Pre-commit checks failed: {e}")

        output = "Pre-commit checks failed\n\n"

        # Add stdout if present
        if e.stdout.strip():
            output += md_codeblock("stdout", e.stdout.rstrip()) + "\n\n"

        # Add stderr if present
        if e.stderr.strip():
            output += md_codeblock("stderr", e.stderr.rstrip()) + "\n\n"

        # Add guidance about automated fixes
        if "files were modified by this hook" in e.stdout:
            output += "Note: Some issues were automatically fixed by the pre-commit hooks. No manual fixes needed for those changes."
        else:
            output += "Note: The above issues require manual fixes as they were not automatically resolved."

        return False, output.strip()
    finally:
        logger.info(
            f"Pre-commit checks completed in {time.monotonic() - start_time:.2f}s"
        )


def handle_precommit_command(ctx: CommandContext) -> Generator[Message, None, None]:
    """Handle the /pre-commit command to manually run pre-commit checks.

    Args:
        ctx: Command context with manager and confirm function

    Yields:
        Messages with pre-commit check results
    """
    # Undo the command message itself
    ctx.manager.undo(1, quiet=True)

    try:
        # Run pre-commit checks on all files
        success, failed_check_message = run_precommit_checks()

        if not success and failed_check_message:
            yield Message("system", failed_check_message, quiet=False)
        elif success:
            yield Message("system", "Pre-commit checks passed ✓")
        else:
            yield Message("system", "Pre-commit checks not enabled or no issues found")

    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.exception(f"Error running pre-commit checks: {e}")
        yield Message("system", f"Pre-commit check failed: {e}")


def run_precommit_on_file(
    log: "Log | None",
    workspace: Path | None,
    path: Path,
    content: str,
    created: bool = False,
) -> Generator[Message, None, None]:
    """Hook function that runs pre-commit on saved files.

    Args:
        path: Path to the saved file
        content: Content that was saved
        created: Whether the file was newly created

    Yields:
        Messages with pre-commit results
    """
    # Check if pre-commit checks should run
    if not use_checks():
        logger.debug("Pre-commit checks not enabled, skipping hook")
        return

    if not run_checks_per_file():
        logger.debug("Per-file pre-commit checks disabled, skipping hook")
        return

    try:
        # Check if pre-commit is available
        check_result = subprocess.run(
            ["pre-commit", "--version"], capture_output=True, text=True, timeout=5
        )
        if check_result.returncode != 0:
            logger.debug("pre-commit not available, skipping hook")
            return

    except KeyboardInterrupt:
        raise
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

    except KeyboardInterrupt:
        raise
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
    return shutil.which("pre-commit") is not None


def run_full_precommit_checks(
    manager: "LogManager",
) -> Generator[Message | StopPropagation, None, None]:
    """Hook function that runs full pre-commit checks after message processing.

    Args:
        manager: Conversation manager with log and workspace

    Yields:
        Messages with pre-commit check results
    """
    # Check if pre-commit checks should run
    if not use_checks():
        logger.debug("Pre-commit checks not enabled, skipping hook")
        return

    # Check if there are modifications

    if not check_for_modifications(manager.log):
        logger.debug("No modifications, skipping pre-commit checks")
        return

    try:
        # Run pre-commit checks on all files
        success, failed_check_message = run_precommit_checks()

        if not success and failed_check_message:
            yield Message("system", failed_check_message, quiet=False)
            # Stop propagation to prevent autocommit from running with failed checks
            yield StopPropagation()
        elif success:
            yield Message("system", "Pre-commit checks passed", hide=True)

    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.exception(f"Error running pre-commit checks: {e}")
        yield Message("system", f"Pre-commit check failed: {e}", hide=True)


# Tool specification
tool = ToolSpec(
    name="precommit",
    desc="Automatic pre-commit checks on file saves and after message processing",
    instructions="""
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
    commands={
        "pre-commit": handle_precommit_command,
    },
    # Note: Tool is enabled by default but hooks check use_checks() to determine if they should run
    # This matches previous behavior where pre-commit runs if GPTME_CHECK=true or .pre-commit-config.yaml exists
)

__all__ = ["tool"]
