"""
Inject AGENTS.md/CLAUDE.md/GEMINI.md files when a tool touches a path in a
directory that has agent instruction files, even if CWD itself didn't change.

This extends ``agents_md_inject`` (which fires on CWD_CHANGED) to also cover
common real workflows where the agent reads or patches a file in a different
directory via an explicit path argument while staying in the same CWD.

Examples:

- ``read projects/gptme/webui/src/foo.ts`` from repo root
- ``patch /tmp/worktrees/feature/file.py`` while CWD is unchanged
- ``save subdir/AGENTS.md`` content in a sibling repo

Subscribes to ``TOOL_EXECUTE_POST`` so the loaded instructions arrive before
the next reasoning step. Reuses ``find_agent_files_in_tree`` and the
``_loaded_agent_files_var`` dedup state from :mod:`agents_md_inject`.

Scope (Phase 1):

- Structured file tools only — ``read``, ``save``, ``append``, ``patch``,
  ``morph``, ``ls``, ``browse``.
- Hard caps to keep the hook cheap: at most ``MAX_PATHS_PER_EVENT``
  directories inspected per tool event and at most ``MAX_NEW_FILES``
  instruction files injected per event.

See: ``knowledge/technical-designs/tool-targeted-agent-instruction-loading.md``
in the Bob repo for the full design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, register_hook
from ..message import Message
from ..prompts import find_agent_files_in_tree
from ..util.context_dedup import _content_hash
from .agents_md_inject import _HASH_PREFIX, _get_loaded_files

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import StopPropagation
    from ..logmanager import Log

logger = logging.getLogger(__name__)

# Tools whose argument shape is structured enough to extract paths from
# without parsing shell strings. Shell payload parsing is explicitly a Phase 2
# concern.
_STRUCTURED_PATH_TOOLS: frozenset[str] = frozenset(
    {
        "read",
        "save",
        "append",
        "patch",
        "morph",
        "ls",
        "browse",
    }
)

# Caps. Keep small — this hook fires on every tool execution.
MAX_PATHS_PER_EVENT = 3
MAX_NEW_FILES = 3


def _extract_paths(tool_use: Any) -> list[str]:
    """Extract candidate path strings from a structured file-tool invocation.

    Phase 1 strategy: take the first positional arg if present, plus any
    string values under ``path``/``filename``/``directory`` keys in kwargs.
    Stop early when ``MAX_PATHS_PER_EVENT`` candidates are collected.
    """
    candidates: list[str] = []
    args = getattr(tool_use, "args", None) or []
    if args:
        first = args[0]
        if isinstance(first, str) and first.strip():
            candidates.append(first.strip())
    kwargs = getattr(tool_use, "kwargs", None) or {}
    for key in ("path", "filename", "directory", "file"):
        val = kwargs.get(key)
        if isinstance(val, str) and val.strip() and val.strip() not in candidates:
            candidates.append(val.strip())
            if len(candidates) >= MAX_PATHS_PER_EVENT:
                break
    return candidates[:MAX_PATHS_PER_EVENT]


def _resolve_directory(path_str: str) -> Path | None:
    """Resolve a tool-supplied path to a real directory to inspect.

    - Absolute paths are used as-is.
    - Relative paths are resolved against the current CWD.
    - If the path points at a file (or doesn't exist), use its parent.
    - Returns ``None`` if the path can't be resolved or escapes obvious bounds.
    """
    try:
        p = Path(path_str).expanduser()
        if not p.is_absolute():
            p = Path(os.getcwd()) / p
        resolved = p.resolve()
    except (OSError, ValueError, RuntimeError):
        return None

    if resolved.is_dir():
        return resolved
    # File or nonexistent — fall back to parent directory, which is a real dir
    # on disk in the common cases (read/save/patch on an existing or new file
    # under an existing directory).
    parent = resolved.parent
    if parent.is_dir():
        return parent
    return None


def on_tool_execute_post(
    log: Log,
    workspace: Path | None,
    tool_use: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Discover and inject AGENTS.md-style files for the tool's target path.

    Args:
        log: The conversation log.
        workspace: Workspace directory path.
        tool_use: The ToolUse object that just executed.
    """
    tool_name = getattr(tool_use, "tool", None)
    if tool_name not in _STRUCTURED_PATH_TOOLS:
        return

    try:
        path_strs = _extract_paths(tool_use)
        if not path_strs:
            return

        # Resolve to directories, dedup while preserving order
        seen_dirs: set[str] = set()
        directories: list[Path] = []
        for path_str in path_strs:
            d = _resolve_directory(path_str)
            if d is None:
                continue
            key = str(d)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            directories.append(d)

        if not directories:
            return

        loaded = _get_loaded_files(log)
        injected = 0

        for directory in directories:
            if injected >= MAX_NEW_FILES:
                break
            new_files = find_agent_files_in_tree(directory, exclude=loaded)
            for agent_file in new_files:
                if injected >= MAX_NEW_FILES:
                    break
                resolved = str(agent_file.resolve())
                if resolved in loaded:
                    continue
                try:
                    content = agent_file.read_text()
                except OSError as e:
                    logger.warning(f"Could not read agent file {agent_file}: {e}")
                    continue

                content_key = f"{_HASH_PREFIX}{_content_hash(content)}"
                if content_key in loaded:
                    logger.debug(
                        f"Skipping {agent_file}: identical content already injected"
                    )
                    loaded.add(resolved)
                    continue

                loaded.add(resolved)
                loaded.add(content_key)

                try:
                    display_path = str(agent_file.resolve().relative_to(Path.home()))
                    display_path = f"~/{display_path}"
                except ValueError:
                    display_path = str(agent_file)

                logger.info(
                    f"Injecting agent instructions from {display_path} "
                    f"(tool={tool_name})"
                )
                injected += 1
                yield Message(
                    "system",
                    f'<agent-instructions source="{display_path}">\n'
                    f"# Agent Instructions ({display_path})\n\n"
                    f"{content}\n"
                    f"</agent-instructions>",
                    files=[agent_file],
                )

    except Exception as e:
        logger.exception(f"Error in tool_target_instructions: {e}")


def register() -> None:
    """Register the tool-target agent-instruction injection hook."""
    register_hook(
        "tool_target_instructions.on_tool_post",
        HookType.TOOL_EXECUTE_POST,
        on_tool_execute_post,
        # Lower priority than cwd_changed.detect (100) so CWD-driven injection
        # gets first crack at any new instruction files.
        priority=10,
    )
    logger.debug("Registered tool-target AGENTS.md injection hook")
