import logging
import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import config_path, get_config, get_project_config
from ..message import Message
from ..util.context import md_codeblock
from ..util.context_dedup import _content_hash
from ..util.tree import get_tree_output
from . import AGENT_FILES, DEFAULT_CONTEXT_FILES, _loaded_agent_files_var
from .context_cmd import get_project_context_cmd_output

_HASH_PREFIX = "ch:"

if TYPE_CHECKING:
    from ..util.uri import FilePath

logger = logging.getLogger(__name__)

CONTEXT_FILE_ORDER: dict[str, int] = {
    "README.md": 10,
    "README.rst": 11,
    "README.txt": 12,
    "README": 13,
    "gptme.toml": 20,
    "AGENTS.md": 30,
    "CLAUDE.md": 31,
    "COPILOT.md": 32,
    "GEMINI.md": 33,
    "pyproject.toml": 40,
    "package.json": 50,
    "Cargo.toml": 60,
    "Makefile": 70,
    "docker-compose.yml": 80,
    "docker-compose.yaml": 81,
}


def _context_file_sort_key(path: Path) -> tuple[int, str]:
    """Sort key for stable prompt-file ordering.

    We keep pattern order from gptme.toml/defaults, but sort each glob's matches
    deterministically so prompt assembly does not depend on filesystem traversal
    order. Known high-signal files get fixed priority, then we fall back to the
    path for lexical stability.
    """
    # tail is "parent/filename" — reserved for future path-qualified keys in
    # CONTEXT_FILE_ORDER (e.g. ".cursor/rules.md"). With current bare-filename
    # keys this lookup is always a miss; the fallback to `name` is what fires.
    tail = "/".join(path.parts[-2:])
    name = path.name
    priority = CONTEXT_FILE_ORDER.get(tail, CONTEXT_FILE_ORDER.get(name, 1000))
    return priority, path.as_posix()


def _sorted_workspace_matches(workspace: Path, fileglob: str) -> list[Path]:
    """Return glob matches in a deterministic order."""
    return sorted(workspace.glob(fileglob), key=_context_file_sort_key)


def _get_git_status(workspace: Path) -> str | None:
    """Get git branch and working tree status for the workspace.

    Returns a formatted string with the current branch and any
    modified/untracked files, or None if not a git repo.
    """
    try:
        # Check if in a git repo
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            check=False,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None

        # Get current branch
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            check=False,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=2,
        )
        branch = (
            branch_result.stdout.strip() if branch_result.returncode == 0 else "unknown"
        )

        # Get short status (modified/untracked files)
        status_result = subprocess.run(
            ["git", "status", "--short"],
            check=False,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status_lines = (
            status_result.stdout.strip() if status_result.returncode == 0 else ""
        )

        if status_lines:
            # Truncate if too many changes (keep it concise)
            lines = status_lines.splitlines()
            if len(lines) > 20:
                shown = "\n".join(lines[:20])
                status_lines = f"{shown}\n... and {len(lines) - 20} more files"
            return f"**Branch:** `{branch}`\n\n{md_codeblock('', status_lines)}"
        return f"**Branch:** `{branch}` (clean)"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        logger.debug(f"Error getting git status: {e}")
        return None


def find_agent_files_in_tree(
    directory: Path,
    exclude: set[str] | None = None,
) -> list[Path]:
    """Find agent instruction files from home down to the given directory.

    Discovers instruction files from multiple AI coding tools (AGENTS.md, CLAUDE.md,
    COPILOT.md, GEMINI.md, .github/copilot-instructions.md, .cursorrules,
    .windsurfrules) for cross-tool compatibility.

    Walks from home -> directory (most general first, most specific last), checking
    each directory for agent instruction files. Returns files whose resolved paths
    are not in the ``exclude`` set.

    Used by both :func:`~gptme.prompts.prompt_workspace` at session start and the
    ``agents_md_inject`` hook mid-session when the CWD changes.
    """
    result: list[Path] = []
    home_dir = Path.home().resolve()
    target = directory.resolve()
    _exclude = exclude or set()

    dirs_to_check: list[Path] = []
    current = target
    while current != current.parent:  # Stop at filesystem root
        dirs_to_check.append(current)
        if current == home_dir:
            break  # Don't go above home directory
        current = current.parent

    # Reverse: most general (home) first, most specific (target) last
    dirs_to_check.reverse()

    # Files authored for other AI tools — loaded for cross-tool compatibility,
    # but users should be aware they may contain instructions not intended for gptme.
    _cross_tool_files = frozenset({".cursorrules", ".windsurfrules"})

    for dir_path in dirs_to_check:
        for filename in AGENT_FILES:
            agent_file = dir_path / filename
            if agent_file.exists():
                resolved = str(agent_file.resolve())
                if resolved not in _exclude:
                    if filename in _cross_tool_files:
                        logger.debug(
                            "Loading cross-tool instruction file %s"
                            " (authored for a different AI tool — instructions may not be gptme-compatible)",
                            agent_file,
                        )
                    result.append(agent_file)

    return result


def prompt_workspace(
    workspace: Path | None = None,
    title="Project Workspace",
    include_path: bool = False,
    include_context_cmd: bool = True,
    include_user_context: bool = True,
) -> Generator[Message, None, None]:
    """Generate the workspace context prompt."""
    # TODO: update this prompt if the files change
    sections = []

    if workspace is None:
        return

    # Add workspace path if requested
    if include_path:
        sections.append(f"**Path:** {workspace.resolve()}")

    project = get_project_config(workspace)

    # Agent instruction files: loaded with tree-walking (most general first, most specific last)
    # These are agent instruction files that should always be included
    # Loading order:
    #   1. User config dir (~/.config/gptme/AGENTS.md) - global defaults
    #   2. Walk from home dir down to workspace, loading any AGENTS.md found
    #      e.g., ~/Programming/AGENTS.md -> ~/Programming/gptme/AGENTS.md -> workspace
    agent_files: list[Path] = []  # Agent instruction files (AGENTS.md, etc.)
    context_files: list[Path] = []  # Regular context files (README, etc.)
    seen_paths: set[str] = set()
    workspace_resolved = workspace.resolve()
    config_dir = Path(config_path).expanduser().resolve().parent

    # Initialize the loaded agent files ContextVar so the agents_md_inject hook
    # (gptme/hooks/agents_md_inject.py) can check which files were already loaded
    # and skip re-injecting them when the CWD changes mid-session.
    loaded_agent_files = _loaded_agent_files_var.get()
    if loaded_agent_files is None:
        loaded_agent_files = set()
        _loaded_agent_files_var.set(loaded_agent_files)

    # 1. Load user-level agent files from ~/.config/gptme/ (global)
    if include_user_context:
        for filename in AGENT_FILES:
            user_file = config_dir / filename
            if user_file.exists():
                resolved = str(user_file.resolve())
                if resolved not in seen_paths:
                    agent_files.append(user_file)
                    seen_paths.add(resolved)
                    loaded_agent_files.add(resolved)
                    try:
                        loaded_agent_files.add(
                            f"{_HASH_PREFIX}{_content_hash(user_file.read_text())}"
                        )
                    except OSError:
                        pass
                    logger.debug(f"Loaded user-level agent file: {user_file}")

    # 2. Walk from home down to workspace, loading any AGENT_FILES found
    #    Uses find_agent_files_in_tree() -- shared with the agents_md_inject hook
    #    Skipped when include_user_context=False: eval workspaces often live under
    #    ~/.local/share/gptme/, so without this guard ~/AGENTS.md would still leak.
    if include_user_context:
        for agent_file in find_agent_files_in_tree(
            workspace_resolved, exclude=seen_paths
        ):
            resolved = str(agent_file.resolve())
            agent_files.append(agent_file)
            seen_paths.add(resolved)
            loaded_agent_files.add(resolved)
            try:
                loaded_agent_files.add(
                    f"{_HASH_PREFIX}{_content_hash(agent_file.read_text())}"
                )
            except OSError:
                pass
            logger.debug(f"Loaded agent file from tree: {agent_file}")

    # Determine which additional file patterns to use (from config or defaults)
    if project is None or project.files is None:
        # No project config or no files specified in config
        file_patterns = DEFAULT_CONTEXT_FILES
        if project is None:
            logger.debug("No project config found, using default context files")
        else:
            logger.debug(
                "Project config has no files specified, using default context files"
            )
    else:
        # Project config exists with files explicitly set (could be empty list)
        file_patterns = project.files
        if not project.files:
            logger.debug(
                "Project config has files explicitly set to empty, not including any files"
            )

    # Process file patterns (additional files from config/defaults)
    for fileglob in file_patterns:
        # expand user
        fileglob = str(Path(fileglob).expanduser())
        # expand with glob
        new_files = _sorted_workspace_matches(workspace, fileglob)
        if new_files:
            for f in new_files:
                # Validate file is within workspace (prevent path traversal)
                try:
                    f.resolve().relative_to(workspace_resolved)
                    resolved = str(f.resolve())
                    # Skip if already loaded (e.g., from agent files)
                    if resolved not in seen_paths:
                        context_files.append(f)
                        seen_paths.add(resolved)
                except ValueError:
                    logger.warning(
                        f"Skipping file outside workspace: {f} (from glob '{fileglob}')"
                    )
        else:
            # Only warn for explicitly configured files, not defaults
            if project and project.files is not None:
                logger.warning(
                    f"File glob '{fileglob}' specified in project config does not match any files."
                )

    # Also include user-level files from ~/.config/gptme/config.toml
    # Resolution rules:
    # - Absolute paths: used as-is
    # - ~ expansion supported
    # - Relative paths: resolved relative to the config directory (e.g. ~/.config/gptme)
    if include_user_context:
        try:
            user_files = (
                get_config().user.prompt.files
                if get_config().user and get_config().user.prompt
                else []
            )
        except (ImportError, OSError, ValueError, AttributeError):
            user_files = []
        if user_files:
            for entry in user_files:
                p = Path(entry).expanduser()
                if not p.is_absolute():
                    p = config_dir / entry
                try:
                    p = p.resolve()
                except OSError:
                    # If resolve fails (e.g., path doesn't exist yet), keep as-is
                    pass
                if p.exists():
                    rp = str(p)
                    if rp not in seen_paths:
                        context_files.append(p)
                        seen_paths.add(rp)
                else:
                    logger.debug(f"User-configured file not found: {p}")

    # Get tree output if enabled
    if tree_output := get_tree_output(workspace):
        sections.append(f"## Project Structure\n\n{md_codeblock('', tree_output)}\n\n")

    # Get git status (branch + working tree changes)
    if git_status := _get_git_status(workspace):
        sections.append(f"## Git Status\n\n{git_status}")

    if sections:
        yield Message("system", f"# {title}\n\n" + "\n\n".join(sections))

    # Yield agent instruction files first with explicit framing
    valid_agent_files: list[FilePath] = [f for f in agent_files if f.exists()]
    if valid_agent_files:
        agent_file_list = "\n".join(f"- {file}" for file in valid_agent_files)
        yield Message(
            "system",
            "## Agent Instructions\n\n"
            "The following files contain user-defined rules, preferences, and workflows. "
            "**You MUST follow these instructions** - they take precedence over default behaviors.\n\n"
            f"{agent_file_list}",
            files=valid_agent_files,
        )

    # Yield context files separately (informational, not directives)
    valid_context_files: list[FilePath] = [f for f in context_files if f.exists()]
    if valid_context_files:
        context_file_list = "\n".join(f"- {file}" for file in valid_context_files)
        yield Message(
            "system",
            f"## Selected files\n\nRead more with `cat`.\n\n{context_file_list}",
            files=valid_context_files,
        )

    # Computed context last (changes most often, least cacheable)
    if (
        include_context_cmd
        and project
        and project.context_cmd
        and (
            cmd_output := get_project_context_cmd_output(project.context_cmd, workspace)
        )
    ):
        yield Message("system", "## Computed context\n\n" + cmd_output)
