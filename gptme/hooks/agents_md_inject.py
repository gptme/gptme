"""
Inject AGENTS.md/CLAUDE.md/GEMINI.md files when the working directory changes.

When the user `cd`s to a new directory during a session, this hook checks if there
are any agent instruction files (AGENTS.md, CLAUDE.md, GEMINI.md) that haven't been
loaded yet. If found, their contents are injected as system messages.

This extends the tree-walking AGENTS.md loading from prompt_workspace() (which runs
at startup) to also work mid-session when the CWD changes.

The set of already-loaded files is shared with prompt_workspace() via the
_loaded_agent_files_var ContextVar defined in prompts.py, which seeds it at startup.

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
from ..prompts import AGENT_FILES, _loaded_agent_files_var

logger = logging.getLogger(__name__)

# Track CWD before tool execution (same pattern as cwd_tracking.py)
_cwd_before_var: ContextVar[str | None] = ContextVar(
    "agents_md_cwd_before", default=None
)


def _get_loaded_files() -> set[str]:
    """Get (or lazily initialize) the loaded agent files set for this context.

    Normally populated by prompt_workspace() at session start. If called before
    that (e.g., in tests), initializes to an empty set.
    """
    files = _loaded_agent_files_var.get()
    if files is None:
        files = set()
        _loaded_agent_files_var.set(files)
    return files


def _find_agent_files_in_tree(directory: Path) -> list[Path]:
    """Find AGENTS.md/CLAUDE.md/GEMINI.md files from home down to the given directory.

    Walks from home → directory (most general first), checking each directory
    for agent instruction files. Only returns files not already in the loaded set.
    """
    new_files: list[Path] = []
    home_dir = Path.home().resolve()
    target = directory.resolve()
    loaded = _get_loaded_files()

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
        for filename in AGENT_FILES:
            agent_file = dir_path / filename
            if agent_file.exists():
                resolved = str(agent_file.resolve())
                if resolved not in loaded:
                    new_files.append(agent_file)

    return new_files


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

        loaded = _get_loaded_files()

        # Read and inject each new file
        for agent_file in new_files:
            resolved = str(agent_file.resolve())
            # Double-check (could have been added by concurrent call)
            if resolved in loaded:
                continue

            try:
                content = agent_file.read_text()
            except OSError as e:
                logger.warning(f"Could not read agent file {agent_file}: {e}")
                continue

            loaded.add(resolved)

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
