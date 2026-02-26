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
from ..prompts import _loaded_agent_files_var, find_agent_files_in_tree

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
        # find_agent_files_in_tree() is shared with prompt_workspace() in prompts.py
        new_files = find_agent_files_in_tree(
            Path(current_cwd), exclude=_get_loaded_files()
        )
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
