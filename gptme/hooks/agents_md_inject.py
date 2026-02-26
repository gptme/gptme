"""
Inject AGENTS.md/CLAUDE.md/GEMINI.md files when the working directory changes.

When the user `cd`s to a new directory during a session, this hook checks if there
are any agent instruction files (AGENTS.md, CLAUDE.md, GEMINI.md) that haven't been
loaded yet. If found, their contents are injected as system messages.

This extends the tree-walking AGENTS.md loading from prompt_workspace() (which runs
at startup) to also work mid-session when the CWD changes.

See: https://github.com/gptme/gptme/issues/1513
"""

import logging
import os
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import ALWAYS_LOAD_FILES

logger = logging.getLogger(__name__)

# Track which agent files have already been loaded (by resolved path).
# Seeded at session start from the initial workspace tree walk,
# then updated as new files are discovered on CWD changes.
_loaded_agent_files: set[str] = set()

# Track CWD before tool execution (same pattern as cwd_tracking.py)
_cwd_before_var: ContextVar[str | None] = ContextVar(
    "agents_md_cwd_before", default=None
)


def _find_agent_files_in_tree(directory: Path) -> list[Path]:
    """Find AGENTS.md/CLAUDE.md/GEMINI.md files from home down to the given directory.

    Walks from home → directory (most general first), checking each directory
    for agent instruction files. Only returns files not already in _loaded_agent_files.
    """
    new_files: list[Path] = []
    home_dir = Path.home().resolve()
    target = directory.resolve()

    # Build path from home to target
    dirs_to_check: list[Path] = []
    current = target
    while current != current.parent:
        dirs_to_check.append(current)
        if current == home_dir:
            break
        current = current.parent

    # Reverse: most general (home) first, most specific (target) last
    dirs_to_check.reverse()

    for dir_path in dirs_to_check:
        for filename in ALWAYS_LOAD_FILES:
            agent_file = dir_path / filename
            if agent_file.exists():
                resolved = str(agent_file.resolve())
                if resolved not in _loaded_agent_files:
                    new_files.append(agent_file)

    return new_files


def session_start_seed(
    logdir: Path,
    workspace: Path | None,
    initial_msgs: list[Message],
) -> Generator[Message | StopPropagation, None, None]:
    """Seed the loaded files set from the initial workspace at session start.

    This ensures we don't re-inject files that were already loaded by prompt_workspace().
    """
    if workspace is None:
        return
        yield  # make generator

    home_dir = Path.home().resolve()
    workspace_resolved = workspace.resolve()

    # Walk the same tree that prompt_workspace() walks
    dirs_to_check: list[Path] = []
    current = workspace_resolved
    while current != current.parent:
        dirs_to_check.append(current)
        if current == home_dir:
            break
        current = current.parent

    for dir_path in dirs_to_check:
        for filename in ALWAYS_LOAD_FILES:
            agent_file = dir_path / filename
            if agent_file.exists():
                resolved = str(agent_file.resolve())
                _loaded_agent_files.add(resolved)

    # Also include user config dir files
    from ..config import config_path

    config_dir = Path(config_path).expanduser().resolve().parent
    for filename in ALWAYS_LOAD_FILES:
        user_file = config_dir / filename
        if user_file.exists():
            _loaded_agent_files.add(str(user_file.resolve()))

    logger.debug(
        f"Seeded {len(_loaded_agent_files)} agent files from initial workspace"
    )

    return
    yield  # make generator


def pre_execute(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Store CWD before tool execution."""
    try:
        _cwd_before_var.set(os.getcwd())
    except Exception as e:
        logger.exception(f"Error in agents_md pre-execute: {e}")

    return
    yield  # make generator


def post_execute(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Check for new AGENTS.md files after CWD changes."""
    try:
        prev_cwd = _cwd_before_var.get()
        if prev_cwd is None:
            return

        current_cwd = os.getcwd()
        if prev_cwd == current_cwd:
            return

        # CWD changed — check for new agent instruction files
        new_files = _find_agent_files_in_tree(Path(current_cwd))
        if not new_files:
            return

        # Read and inject each new file
        for agent_file in new_files:
            resolved = str(agent_file.resolve())
            # Double-check (could have been added by concurrent call)
            if resolved in _loaded_agent_files:
                continue

            try:
                content = agent_file.read_text()
            except OSError as e:
                logger.warning(f"Could not read agent file {agent_file}: {e}")
                continue

            _loaded_agent_files.add(resolved)

            # Make the path relative to home for cleaner display
            try:
                display_path = str(agent_file.resolve().relative_to(Path.home()))
                display_path = f"~/{display_path}"
            except ValueError:
                display_path = str(agent_file)

            logger.info(f"Injecting agent instructions from {display_path}")
            yield Message(
                "system",
                f'<agent-instructions source="{display_path}">\n'
                f"# Agent Instructions ({display_path})\n\n"
                f"{content}\n"
                f"</agent-instructions>",
                files=[agent_file],
            )

    except Exception as e:
        logger.exception(f"Error in agents_md post-execute: {e}")


def register() -> None:
    """Register the AGENTS.md injection hooks."""
    register_hook(
        "agents_md_inject.session_start",
        HookType.SESSION_START,
        session_start_seed,
        priority=50,  # After workspace_agents but before most hooks
    )
    register_hook(
        "agents_md_inject.pre_execute",
        HookType.TOOL_EXECUTE_PRE,
        pre_execute,
        priority=0,
    )
    register_hook(
        "agents_md_inject.post_execute",
        HookType.TOOL_EXECUTE_POST,
        post_execute,
        priority=0,
    )
    logger.debug("Registered AGENTS.md injection hooks")
