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

In server mode (Flask), ContextVars don't propagate across HTTP request contexts, so
_loaded_agent_files_var starts as None on each request. To avoid re-injecting
already-loaded files, _get_loaded_files() falls back to scanning the conversation log
for <agent-instructions> system messages when the ContextVar is empty.

See: https://github.com/gptme/gptme/issues/1513
See: https://github.com/gptme/gptme/issues/1521
See: https://github.com/gptme/gptme/issues/1958
"""

import logging
import re
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import _loaded_agent_files_var, find_agent_files_in_tree
from ..util.context_dedup import _content_hash

# Prefix used to store content hashes (vs file paths) in _loaded_agent_files_var.
# Prevents path-identical-content re-injection when cwd changes to a git worktree.
_HASH_PREFIX = "ch:"

logger = logging.getLogger(__name__)


def _derive_loaded_files_from_log(log: Log) -> set[str]:
    """Scan the conversation log for already-injected agent instruction files.

    Used in server mode where ContextVars don't propagate across HTTP request
    contexts, causing _loaded_agent_files_var to start as None each request.
    Parses <agent-instructions source="..."> tags in system messages to rebuild
    the loaded-files set from the persistent conversation state.

    Also records content hashes (prefixed with ``ch:``) so that worktree copies
    with the same content but a different path are not re-injected.
    """
    loaded: set[str] = set()
    for msg in log.messages:
        if msg.role == "system":
            # Extract source paths from opening tags (works even if closing tag missing).
            for path_match in re.finditer(
                r'<agent-instructions source="([^"]+)">', msg.content
            ):
                path_str = path_match.group(1)
                try:
                    resolved = str(Path(path_str).expanduser().resolve())
                    loaded.add(resolved)
                except (OSError, ValueError):
                    loaded.add(path_str)
            # Extract content hashes from complete blocks so worktree copies with
            # identical content are also skipped.
            for block_match in re.finditer(
                r'<agent-instructions source="[^"]*">(.*?)</agent-instructions>',
                msg.content,
                re.DOTALL,
            ):
                loaded.add(f"{_HASH_PREFIX}{_content_hash(block_match.group(1))}")
    return loaded


def _get_loaded_files(log: Log | None = None) -> set[str]:
    """Get (or lazily initialize) the loaded agent files set for this context.

    Normally populated by prompt_workspace() at session start. In server mode,
    the ContextVar starts as None on each request (ContextVars don't propagate
    across Flask request contexts). When the ContextVar is empty and a log is
    provided, falls back to scanning the log for already-injected files to avoid
    re-injection after CWD changes.
    """
    files = _loaded_agent_files_var.get()
    if files is None:
        files = _derive_loaded_files_from_log(log) if log is not None else set()
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
        # Pass the log so server mode can derive loaded files from conversation history
        # when the ContextVar is empty (ContextVars reset per Flask request context).
        new_files = find_agent_files_in_tree(
            Path(new_cwd), exclude=_get_loaded_files(log)
        )
        if not new_files:
            return

        loaded = _get_loaded_files(log)

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

            # Skip if identical content was already injected from a different path
            # (e.g. switching cwd to a git worktree that shares the same AGENTS.md).
            content_key = f"{_HASH_PREFIX}{_content_hash(content)}"
            if content_key in loaded:
                logger.debug(
                    f"Skipping {agent_file}: identical content already injected"
                )
                loaded.add(resolved)
                continue

            loaded.add(resolved)
            loaded.add(content_key)

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
