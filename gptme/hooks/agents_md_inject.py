"""
Inject AGENTS.md/CLAUDE.md/GEMINI.md files when the working directory changes.

When the user `cd`s to a new directory during a session, this hook checks if there
are any agent instruction files (AGENTS.md, CLAUDE.md, GEMINI.md) that haven't been
loaded yet. If found, their contents are injected as system messages.

This extends the tree-walking AGENTS.md loading from prompt_workspace() (which runs
at startup) to also work mid-session when the CWD changes.

The set of already-loaded files is shared with prompt_workspace() via the
_loaded_agent_files_var ContextVar defined in prompts.py, which seeds it at startup.

Subscribes to the centralized CWD_CHANGED hook type instead of independently
tracking pre/post CWD values.

See: https://github.com/gptme/gptme/issues/1513
See: https://github.com/gptme/gptme/issues/1521
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import _loaded_agent_files_var, find_agent_files_in_tree

logger = logging.getLogger(__name__)


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


def on_cwd_changed(
    log: Log,
    workspace: Path | None,
    old_cwd: str,
    new_cwd: str,
    tool_use: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Check for new AGENTS.md files after CWD changes.

    Args:
        log: The conversation log
        workspace: Workspace directory path
        old_cwd: Previous working directory
        new_cwd: New working directory
        tool_use: The tool that caused the change
    """
    try:
        # find_agent_files_in_tree() is shared with prompt_workspace() in prompts.py
        new_files = find_agent_files_in_tree(Path(new_cwd), exclude=_get_loaded_files())
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
        logger.exception(f"Error in agents_md on CWD change: {e}")


def register() -> None:
    """Register the AGENTS.md injection hook."""
    register_hook(
        "agents_md_inject.on_cwd_change",
        HookType.CWD_CHANGED,
        on_cwd_changed,
        priority=0,
    )
    logger.debug("Registered AGENTS.md injection hook")
