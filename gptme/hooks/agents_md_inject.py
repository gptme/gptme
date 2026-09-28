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

Agent-identity guard: instruction files inside a *different gptme agent workspace*
(a directory whose ``gptme.toml`` declares an ``[agent]`` name other than the
current session's agent) are never injected. In a multi-agent setup each agent's
workspace ``AGENTS.md`` typically defines that agent's identity, git identity and
operational rules, so loading it because of a ``cd`` would silently hand the
session another agent's persona. A short visible notice is emitted instead, once
per foreign workspace. Instruction files elsewhere (other projects, subdirectories
of the current workspace) still load as before, and each load is announced with a
short visible message naming the file.

See: https://github.com/gptme/gptme/issues/1513
See: https://github.com/gptme/gptme/issues/1521
See: https://github.com/gptme/gptme/issues/1958
"""

import logging
import re
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Any

from ..config import get_config, get_project_config
from ..hooks import HookType, StopPropagation, register_hook
from ..logmanager import Log
from ..message import Message
from ..prompts import _loaded_agent_files_var, find_agent_files_in_tree
from ..util.context_dedup import _content_hash

# Prefix used to store content hashes (vs file paths) in _loaded_agent_files_var.
# Prevents path-identical-content re-injection when cwd changes to a git worktree.
_HASH_PREFIX = "ch:"

# Prefix used to record foreign agent workspaces already reported in
# _loaded_agent_files_var, so the "not loaded" notice is emitted only once.
_FOREIGN_PREFIX = "foreign-agent:"

_FOREIGN_TAG = "agent-workspace-skipped"

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
            # Foreign agent workspaces already reported (notice emitted once).
            for skip_match in re.finditer(
                rf'<{_FOREIGN_TAG} path="([^"]+)"', msg.content
            ):
                try:
                    root = str(Path(skip_match.group(1)).expanduser().resolve())
                except (OSError, ValueError):
                    root = skip_match.group(1)
                loaded.add(f"{_FOREIGN_PREFIX}{root}")
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


def _format_display_path(agent_file: Path) -> str:
    """Format an agent file path for transcript display."""
    try:
        relative = agent_file.resolve().relative_to(Path.home())
        return f"~/{relative}"
    except ValueError:
        return str(agent_file.resolve())


def _agent_name_of(directory: Path) -> str | None:
    """Return the ``[agent]`` name declared by ``directory``'s gptme.toml, if any.

    Only consults the project config when a ``gptme.toml`` actually exists, so
    walking many plain directories doesn't churn the project-config cache.
    """
    if not (
        (directory / "gptme.toml").is_file()
        or (directory / ".github" / "gptme.toml").is_file()
    ):
        return None
    try:
        config = get_project_config(directory, quiet=True)
    except Exception as e:  # a malformed config shouldn't break the hook
        logger.debug(f"Could not load project config in {directory}: {e}")
        return None
    if config and config.agent and config.agent.name:
        return config.agent.name
    return None


def _session_identity(workspace: Path | None) -> tuple[str | None, set[Path]]:
    """Return the session's agent name and its own workspace roots.

    The agent name comes from the chat config's agent workspace when available,
    falling back to the session workspace's ``gptme.toml``. Own roots are the
    session workspace and agent path (resolved); an agent workspace at or above
    one of them is part of the session's own context, never foreign.
    """
    roots: set[Path] = set()
    name: str | None = None
    try:
        chat = get_config().chat
        if chat is not None and chat.agent is not None:
            roots.add(chat.agent.resolve())
            agent_config = chat.agent_config
            if agent_config and agent_config.name:
                name = agent_config.name
    except Exception as e:  # config problems shouldn't break the hook
        logger.debug(f"Could not determine session agent from chat config: {e}")
    if workspace is not None:
        ws = workspace.resolve()
        roots.add(ws)
        if name is None:
            name = _agent_name_of(ws)
    return name, roots


def _find_foreign_agent_root(
    agent_file: Path, session_name: str | None, own_roots: set[Path]
) -> tuple[Path, str] | None:
    """Find the foreign agent workspace that ``agent_file`` belongs to, if any.

    Walks up from the file's directory (stopping at home or the filesystem root)
    to the nearest directory whose gptme.toml declares an ``[agent]`` name. That
    workspace is *foreign* when it is not one of the session's own roots (or an
    ancestor of one) and its agent name differs from the session's. A session
    without an agent name treats every other agent workspace as foreign.
    """
    home = Path.home().resolve()
    current = agent_file.resolve().parent
    while True:
        name = _agent_name_of(current)
        if name is not None:
            if any(root.is_relative_to(current) for root in own_roots):
                return None
            if session_name is not None and name == session_name:
                return None
            return current, name
        if current == home or current == current.parent:
            return None
        current = current.parent


def _format_foreign_notice(
    root: Path, name: str, files: list[Path], session_name: str | None
) -> Message:
    """Build the visible notice for instructions skipped in a foreign workspace."""
    display_root = _format_display_path(root)
    display_files = ", ".join(_format_display_path(f) for f in files)
    if session_name:
        reason = (
            "it defines a different agent identity than this session's "
            f"'{session_name}'"
        )
    else:
        reason = "it defines a different agent identity"
    return Message(
        "system",
        f'<{_FOREIGN_TAG} path="{display_root}" agent="{name}">\n'
        f"Entered {display_root}, which is agent workspace '{name}'; "
        f"its instructions were not loaded ({reason}). "
        f"Skipped: {display_files}. "
        "Do not adopt that agent's identity; read the files explicitly only "
        "if the task requires it.\n"
        f"</{_FOREIGN_TAG}>",
    )


def _partition_foreign(
    agent_files: list[Path], workspace: Path | None
) -> tuple[list[Path], dict[Path, tuple[str, list[Path]]], str | None]:
    """Split candidates into allowed files and files in foreign agent workspaces."""
    session_name, own_roots = _session_identity(workspace)
    allowed: list[Path] = []
    foreign: dict[Path, tuple[str, list[Path]]] = {}
    for agent_file in agent_files:
        found = _find_foreign_agent_root(agent_file, session_name, own_roots)
        if found is None:
            allowed.append(agent_file)
        else:
            root, name = found
            foreign.setdefault(root, (name, []))[1].append(agent_file)
    return allowed, foreign, session_name


def inject_agent_instruction_files(
    log: Log | None,
    agent_files: Iterable[Path],
    *,
    workspace: Path | None = None,
    max_files: int | None = None,
    max_bytes: int | None = None,
    skip_tag: str = "agent-instructions-skipped",
) -> Generator[Message, None, None]:
    """Inject discovered agent instruction files with shared dedup semantics.

    Files inside a different gptme agent workspace are never injected; a short
    visible notice is emitted instead (once per foreign workspace). Each
    injected file is followed by a short visible message naming it, since the
    injected content itself is display-hidden.

    Args:
        log: Conversation log used for server-mode dedup fallback.
        agent_files: Candidate AGENTS/CLAUDE/GEMINI files to inject.
        workspace: Session workspace, used to determine the session's own
            agent identity and roots.
        max_files: Optional per-call cap on injected files.
        max_bytes: Optional per-call cap on combined injected content size.
        skip_tag: XML-ish tag used for skip-note system messages.
    """
    loaded = _get_loaded_files(log)
    seen_candidates: set[str] = set()
    injected = 0
    bytes_injected = 0

    candidates = [f for f in agent_files if str(f.resolve()) not in loaded]
    if not candidates:
        return

    allowed, foreign, session_name = _partition_foreign(candidates, workspace)
    for root, (name, files) in foreign.items():
        logger.info(
            "Not loading instructions from agent workspace '%s' (%s): %s",
            name,
            root,
            ", ".join(str(f) for f in files),
        )
        key = f"{_FOREIGN_PREFIX}{root}"
        if key in loaded:
            continue
        loaded.add(key)
        yield _format_foreign_notice(root, name, files, session_name)

    for agent_file in allowed:
        resolved = str(agent_file.resolve())
        if resolved in loaded or resolved in seen_candidates:
            continue
        seen_candidates.add(resolved)

        try:
            content = agent_file.read_text()
        except OSError as e:
            logger.warning(f"Could not read agent file {agent_file}: {e}")
            continue

        # Skip if identical content was already injected from a different path
        # (e.g. switching cwd to a git worktree that shares the same AGENTS.md).
        content_key = f"{_HASH_PREFIX}{_content_hash(content)}"
        if content_key in loaded:
            logger.debug(f"Skipping {agent_file}: identical content already injected")
            loaded.add(resolved)
            continue

        display_path = _format_display_path(agent_file)
        content_bytes = len(content.encode("utf-8"))

        if max_files is not None and injected >= max_files:
            logger.info(
                "Skipping %s: reached per-event injection cap (%d)",
                display_path,
                max_files,
            )
            yield Message(
                "system",
                f'<{skip_tag} source="{display_path}">\n'
                f"Skipped: reached per-event injection cap of {max_files} files.\n"
                f"</{skip_tag}>",
            )
            continue

        if max_bytes is not None and bytes_injected + content_bytes > max_bytes:
            logger.info(
                "Skipping %s: would exceed per-event budget (%d bytes)",
                display_path,
                max_bytes,
            )
            yield Message(
                "system",
                f'<{skip_tag} source="{display_path}">\n'
                f"Skipped: would exceed per-event budget of {max_bytes} bytes.\n"
                f"</{skip_tag}>",
            )
            continue

        loaded.add(resolved)
        loaded.add(content_key)
        injected += 1
        bytes_injected += content_bytes

        logger.info(f"Injecting agent instructions from {display_path}")
        yield Message(
            "system",
            f'<agent-instructions source="{display_path}">\n'
            f"# Agent Instructions ({display_path})\n\n"
            f"{content}\n"
            f"</agent-instructions>",
            files=[agent_file],
            # Display-only hide: the instructions still reach the LLM, but this
            # injected context message shouldn't clutter the conversation view
            # (it's transient/not persisted, so it otherwise only appeared live
            # via the message_added stream event and vanished on reload).
            hide=True,
        )
        # The instructions are hidden from the conversation view, so announce
        # the load visibly: the user should see which file entered the context,
        # not only the model.
        yield Message(
            "system",
            f"Loaded agent instructions from {display_path} ({content_bytes} bytes).",
        )


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
        new_files = find_agent_files_in_tree(
            Path(new_cwd), exclude=_get_loaded_files(log)
        )
        if not new_files:
            return
        yield from inject_agent_instruction_files(log, new_files, workspace=workspace)

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
