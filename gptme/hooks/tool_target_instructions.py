"""
Inject AGENTS.md/CLAUDE.md/GEMINI.md files when tools touch a directory.

When the agent reads or writes files in a directory that has local agent
instruction files (AGENTS.md, CLAUDE.md, GEMINI.md), this hook discovers
and injects them — even when cwd hasn't changed.

This complements ``agents_md_inject`` (which fires on cwd changes) by
covering the common case where an agent touches a subdirectory or worktree
without ``cd`` first.

See: knowledge/technical-designs/tool-targeted-agent-instruction-loading.md
"""

import logging
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import _loaded_agent_files_var, find_agent_files_in_tree
from ..tools.base import ToolUse
from ..util.context_dedup import _content_hash

logger = logging.getLogger(__name__)

# Path-like parameter names to extract from structured tool kwargs/args
_PATH_PARAM_NAMES: frozenset[str] = frozenset(
    {"path", "paths", "file", "files", "directory", "cwd"}
)

# Maximum injected instruction files per tool event
_MAX_INJECTIONS_PER_EVENT = 3

# Prefix used for content-hash dedup keys (shared with agents_md_inject)
_HASH_PREFIX = "ch:"

# Tag format for injected instructions (reused from agents_md_inject)
_INJECTION_TAG = '<agent-instructions source="{display_path}">'


def _extract_tool_paths(tool_use: Any) -> list[Path]:
    """Extract path-like targets from a structured tool use's kwargs.

    Only uses kwargs (not positional args) to avoid false positives
    from tools like ``shell`` where args[0] is a command string.
    """
    paths: list[Path] = []

    kwargs = getattr(tool_use, "kwargs", None) or {}

    candidates: list[str] = [
        kwargs[key]
        for key in _PATH_PARAM_NAMES
        if key in kwargs and isinstance(kwargs[key], str)
    ]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            resolved = Path(candidate).expanduser()
            if not resolved.is_absolute():
                resolved = Path(os.getcwd(), candidate).resolve()
            else:
                resolved = resolved.resolve()
        except (OSError, ValueError):
            continue
        paths.append(resolved)

    return paths


def _get_existing_injected_files(log: Log) -> set[str]:
    """Get already-injected instruction file paths and content hashes.

    Uses the shared ``_loaded_agent_files_var`` ContextVar, which is
    populated by ``prompt_workspace()`` at session start and extended
    by ``agents_md_inject`` on CWD changes.

    Falls back to scanning the log for path-based dedup when the
    ContextVar is empty (server mode).
    """
    loaded = _loaded_agent_files_var.get()
    if loaded is not None:
        return loaded

    # Fallback: derive paths from log messages (server mode)
    import re

    loaded = set()
    for msg in log.messages:
        if msg.role != "system":
            continue
        for path_match in re.finditer(
            r'<agent-instructions source="([^"]+)">', msg.content
        ):
            path_str = path_match.group(1)
            try:
                resolved = str(Path(path_str).expanduser().resolve())
                loaded.add(resolved)
            except (OSError, ValueError):
                loaded.add(path_str)
    _loaded_agent_files_var.set(loaded)
    return loaded


def _resolve_parent_dirs(paths: list[Path]) -> list[Path]:
    """Convert file paths to their parent directories, deduplicating.

    For explicit directory paths, keep them as-is.
    """
    seen: set[str] = set()
    result: list[Path] = []
    for p in paths:
        if p.is_dir():
            target = p
        else:
            target = p.parent
        key = str(target)
        if key not in seen:
            seen.add(key)
            result.append(target)
    return result


def on_tool_execute_post(
    log: Log,
    workspace: Path | None,
    tool_use: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """After tool execution, discover and inject agent instruction files.

    Only fires for structured tools with path-like kwargs. Skips
    directories that have no undiscovered instruction files.
    """
    try:
        if not isinstance(tool_use, ToolUse):
            return

        # Phase 1 scope: only structured file tools with path kwargs
        target_paths = _extract_tool_paths(tool_use)
        if not target_paths:
            return

        dirs = _resolve_parent_dirs(target_paths)
        if not dirs:
            return

        existing = _get_existing_injected_files(log)
        all_new: list[tuple[Path, Path]] = []  # (dir, resolved_file)
        for d in dirs:
            candidates = find_agent_files_in_tree(d, exclude=existing)
            for cf in candidates:
                resolved = str(cf.resolve())
                if resolved not in existing:
                    all_new.append((d, cf))

        if not all_new:
            return

        # Apply per-event cap
        injected_count = 0
        for _d, agent_file in all_new[:_MAX_INJECTIONS_PER_EVENT]:
            resolved = str(agent_file.resolve())
            if resolved in existing:
                continue

            try:
                content = agent_file.read_text()
            except OSError as e:
                logger.warning(f"Could not read agent file {agent_file}: {e}")
                continue

            # Content-hash dedup
            content_key = f"{_HASH_PREFIX}{_content_hash(content)}"
            if content_key in existing:
                logger.debug(
                    f"Skipping {agent_file}: identical content already injected"
                )
                existing.add(resolved)
                continue

            existing.add(resolved)
            existing.add(content_key)

            # Display path relative to home when possible
            try:
                display_path = str(agent_file.resolve().relative_to(Path.home()))
                display_path = f"~/{display_path}"
            except ValueError:
                display_path = str(agent_file)

            logger.info(
                "Injecting tool-targeted agent instructions from %s (touched by %s)",
                display_path,
                tool_use.tool,
            )
            yield Message(
                "system",
                f"{_INJECTION_TAG.format(display_path=display_path)}\n"
                f"# Agent Instructions ({display_path})\n\n"
                f"{content}\n"
                f"</agent-instructions>",
                files=[agent_file],
            )
            injected_count += 1

        if len(all_new) > _MAX_INJECTIONS_PER_EVENT:
            skipped = len(all_new) - _MAX_INJECTIONS_PER_EVENT
            logger.debug(
                "Skipped %d agent instruction files (per-event cap %d)",
                skipped,
                _MAX_INJECTIONS_PER_EVENT,
            )

    except Exception as e:
        logger.exception(f"Error in tool_target_instructions: {e}")


def register() -> None:
    """Register the tool-targeted instruction loading hook."""
    register_hook(
        "tool_target_instructions.on_tool_execute_post",
        HookType.TOOL_EXECUTE_POST,
        on_tool_execute_post,
        priority=50,  # Run after CWD change detection (100) but early
    )
    logger.debug("Registered tool-targeted instruction loading hook")
