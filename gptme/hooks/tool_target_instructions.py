"""
Inject AGENTS.md / CLAUDE.md / GEMINI.md files when tools touch new directories.

Extends `agents_md_inject` from cwd-aware to tool-target-aware: when a structured
file tool (read, save, append, patch, etc.) references a path outside the
already-loaded directory tree, this hook discovers local agent instruction files
near that path and injects them before the next reasoning step.

Reuses the existing agent-file discovery (`find_agent_files_in_tree`) and the
`_loaded_agent_files_var` dedup set populated by `prompt_workspace()` and
`agents_md_inject`. Identical-content files (worktree copies) are skipped via
content-hash dedup.

Phase 1: structured file tools only (read/save/append/patch/morph). Shell command
parsing is intentionally out of scope — `cwd.changed` covers that.

See: knowledge/technical-designs/tool-targeted-agent-instruction-loading.md
"""

import logging
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import find_agent_files_in_tree
from .agents_md_inject import _get_loaded_files, inject_agent_instruction_files

if TYPE_CHECKING:
    from ..tools.base import ToolUse  # fmt: skip

logger = logging.getLogger(__name__)

# Tools whose arguments name a target file or directory. Free-form tools
# (shell, ipython, browser_query) are excluded — their target is implicit
# and would produce too many false positives.
_PATH_TOOLS: frozenset[str] = frozenset(
    {
        "save",
        "append",
        "patch",
        "morph",
        "read",
    }
)

# kwarg keys that contain explicit path-like values for the tools above.
_PATH_KWARG_KEYS: tuple[str, ...] = (
    "path",
    "paths",
    "file",
    "files",
    "directory",
    "cwd",
)

# Hard caps per tool event — keep the hook fast and prevent prompt-cache thrash.
_MAX_INJECT_PER_EVENT = 3
_MAX_BYTES_PER_EVENT = 12_000


def _extract_paths(tool_use: "ToolUse") -> list[Path]:
    """Extract explicit file/directory path candidates from a tool's arguments.

    Returns expanded `Path` objects for downstream resolution. Free text and
    unknown tools yield an empty list — phase 1 keeps the extractor strict.
    """
    if tool_use is None or tool_use.tool not in _PATH_TOOLS:
        return []

    candidates: list[str] = []

    # 1. Structured kwargs from tool/function-calling formats.
    if tool_use.kwargs:
        for key in _PATH_KWARG_KEYS:
            value = tool_use.kwargs.get(key)
            if not value:
                continue
            # `paths` / `files` may be newline- or comma-separated.
            if key in ("paths", "files"):
                for part in str(value).replace(",", "\n").splitlines():
                    part = part.strip()
                    if part:
                        candidates.append(part)
            else:
                candidates.append(str(value))

    # 2. Positional args from markdown-format tool blocks. The first arg is
    #    conventionally the path for save/append/patch/morph/read.
    if not candidates and tool_use.args:
        first = tool_use.args[0].strip()
        if first:
            candidates.append(first)

    # 3. Markdown-format batch reads put paths in the content block rather
    #    than positional args. Support one-path-per-line with comment lines.
    if not candidates and tool_use.tool == "read" and tool_use.content:
        for line in tool_use.content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                candidates.append(line)

    paths: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        try:
            path = Path(raw).expanduser()
        except (OSError, ValueError):
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


def _candidate_directories(paths: list[Path]) -> list[Path]:
    """Resolve each input path to a directory to scan.

    File paths → parent directory. Directory paths → themselves. Non-existent
    paths are still resolved so we walk what the agent *intended* to touch.
    Duplicate directories are deduped while preserving order.
    """
    dirs: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            continue
        if resolved.is_dir():
            directory = resolved
        elif resolved.exists():
            directory = resolved.parent
        else:
            # For unborn files (e.g. about-to-be-saved), use the parent.
            directory = resolved.parent
        key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        dirs.append(directory)
    return dirs


def on_tool_execute_post(
    log: Log,
    workspace: Path | None,
    tool_use: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Discover and inject AGENTS.md files for paths touched by structured tools.

    Args:
        log: The conversation log (used as a fallback for the loaded-files set
            in server mode, mirroring `agents_md_inject`).
        workspace: Workspace directory path.
        tool_use: The tool that just executed.
    """
    try:
        paths = _extract_paths(tool_use)
        if not paths:
            return

        candidate_dirs = _candidate_directories(paths)
        if not candidate_dirs:
            return

        loaded = _get_loaded_files(log)
        candidate_files: list[Path] = []
        seen_files: set[str] = set()

        for directory in candidate_dirs:
            new_files = find_agent_files_in_tree(directory, exclude=loaded)
            if not new_files:
                continue

            for agent_file in new_files:
                resolved = str(agent_file.resolve())
                if resolved in seen_files:
                    continue
                seen_files.add(resolved)
                candidate_files.append(agent_file)

        if not candidate_files:
            return

        # Reverse so most-specific (deepest) files come first — when the
        # per-event cap fires, general/home-level files are dropped rather
        # than the project-level ones the tool is actually targeting.
        yield from inject_agent_instruction_files(
            log,
            reversed(candidate_files),
            max_files=_MAX_INJECT_PER_EVENT,
            max_bytes=_MAX_BYTES_PER_EVENT,
        )

    except Exception as e:
        logger.exception(f"Error in tool_target_instructions hook: {e}")


def register() -> None:
    """Register the tool-target agent instruction injection hook."""
    register_hook(
        "tool_target_instructions.on_tool_post",
        HookType.TOOL_EXECUTE_POST,
        on_tool_execute_post,
        # Lower priority than cwd_changed.detect (100) so cwd-driven loading
        # runs first when a tool both changes cwd and touches a path.
        priority=10,
    )
    logger.debug("Registered tool_target_instructions hook")
