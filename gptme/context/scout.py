"""Context-scout pre-pass: cheap model identifies relevant files before the main turn.

When ``[context] scout_model`` is configured in gptme.toml, a fast cheap model
receives the file tree + user message and returns the paths that are most
relevant. Those files are read and injected as a system message before the
main model runs, so the main model never wastes tokens on exploratory
file-finding.

Pattern is borrowed from freebuff (CodebuffAI/freebuff), whose file-lister
outputs plain newline-separated paths with no commentary.

See: https://github.com/gptme/gptme/issues/3652
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

from ..message import Message

logger = logging.getLogger(__name__)

_SCOUT_SENTINEL = "<!-- gptme-context-scout -->"

# Skip the scout if the user message is very short (likely a follow-up command,
# not a new coding task).
_MIN_USER_MESSAGE_WORDS = 20

# Maximum number of paths the scout may return; excess paths are silently dropped.
_MAX_SCOUT_FILES = 20

_SCOUT_SYSTEM_PROMPT = """\
You are a file-relevance oracle. You receive a repository file list and a user
request. Your ONLY job: output the paths of files that are most relevant to
fulfil the request. No prose, no explanation, no commentary. Output one
relative path per line, nothing else. If no file is clearly relevant, output
nothing.\
"""


def _get_messages_from_manager(manager: Any) -> list[Message]:
    """Extract messages from a LogManager or Log object."""
    if manager is None:
        return []
    # LogManager.log is a Log with .messages; also accept a Log passed as manager,
    # or a plain list if a caller copied the log for a step.
    log = getattr(manager, "log", manager)
    if isinstance(log, list):
        return list(log) if log else []
    msgs = getattr(log, "messages", None)
    return list(msgs) if msgs else []


def _build_file_tree(workspace: Path, max_paths: int = 500) -> str:
    """Build a compact newline-separated file list from the workspace."""
    from .selector.file_selector import get_workspace_files

    files = get_workspace_files(workspace)
    paths = sorted(str(f.relative_to(workspace)) for f in files)
    if len(paths) > max_paths:
        paths = paths[:max_paths]
    return "\n".join(paths)


def scout_files(
    user_message: str,
    workspace: Path,
    scout_model: str,
) -> list[Path]:
    """Run the cheap scout model and return a list of relevant file paths.

    Returns an empty list on any error so callers degrade gracefully.
    """
    from ..llm import reply  # fmt: skip

    file_tree = _build_file_tree(workspace)
    if not file_tree:
        return []

    scout_prompt = (
        f"<file_tree>\n{file_tree}\n</file_tree>\n\n"
        f"<request>\n{user_message}\n</request>"
    )

    messages = [
        Message("system", _SCOUT_SYSTEM_PROMPT),
        Message("user", scout_prompt),
    ]

    try:
        response = reply(
            messages=messages,
            model=scout_model,
            workspace=None,  # Scout has no workspace context of its own
            stream=False,
        )
    except Exception:
        logger.debug("context-scout call failed", exc_info=True)
        return []

    # Parse response: one path per line, ignore blank lines and comments
    raw_text = response.content if isinstance(response.content, str) else ""
    candidate_lines = [line.strip() for line in raw_text.splitlines()]
    candidate_lines = [ln for ln in candidate_lines if ln and not ln.startswith("#")]

    paths: list[Path] = []
    for line in candidate_lines[:_MAX_SCOUT_FILES]:
        # Scout returns relative paths; resolve against workspace
        p = workspace / line if not Path(line).is_absolute() else Path(line)
        try:
            p = p.resolve()
        except OSError:
            continue
        # Guard: path must be inside workspace and exist
        try:
            p.relative_to(workspace.resolve())
        except ValueError:
            logger.debug("context-scout: ignoring path outside workspace: %s", p)
            continue
        if p.exists() and p.is_file():
            paths.append(p)

    logger.debug("context-scout found %d relevant file(s)", len(paths))
    return paths


def _make_turn_pre_hook(scout_model: str, workspace: Path):
    """Return a TURN_PRE hook generator bound to the given scout_model."""

    def _scout_hook(
        manager: Any = None,
        **kwargs: Any,
    ) -> Generator[Message, None, None]:
        msgs = _get_messages_from_manager(manager)

        # Find the last user message
        user_msgs = [m for m in msgs if getattr(m, "role", None) == "user"]
        if not user_msgs:
            return

        last_user_content = getattr(user_msgs[-1], "content", "") or ""
        if not isinstance(last_user_content, str):
            return

        # Skip very short messages (likely follow-up commands)
        if len(last_user_content.split()) < _MIN_USER_MESSAGE_WORDS:
            return

        # Skip if we already injected scout context recently in this conversation
        recent = msgs[-10:]
        if any(
            _SCOUT_SENTINEL in (getattr(m, "content", "") or "")
            for m in recent
            if getattr(m, "role", None) == "system"
        ):
            return

        files = scout_files(last_user_content, workspace, scout_model)
        if not files:
            return

        # Build a system message with the file contents
        parts = [_SCOUT_SENTINEL, "**Context-scout pre-loaded files:**\n"]
        for fpath in files:
            try:
                content = fpath.read_text(errors="replace")
                rel = fpath.relative_to(workspace.resolve())
                parts.append(f"\n### `{rel}`\n```\n{content}\n```")
            except OSError:
                continue

        yield Message("system", "\n".join(parts), hide=False)

    return _scout_hook


def register() -> None:
    """Register the context-scout TURN_PRE hook if configured."""
    from ..config import get_config  # fmt: skip
    from ..hooks import HookType, register_hook  # fmt: skip

    config = get_config()
    context_cfg = getattr(config, "context", None)
    if context_cfg is None:
        return

    scout_model: str | None = getattr(context_cfg, "scout_model", None)
    if not scout_model:
        return

    workspace: Path | None = None
    if config.chat is not None:
        workspace = getattr(config.chat, "workspace", None)
    if workspace is None and config.project is not None:
        workspace = getattr(config.project, "_workspace", None)
    if workspace is None:
        workspace = Path.cwd()

    hook_fn = _make_turn_pre_hook(scout_model, workspace)
    register_hook(
        "context_scout.turn_pre",
        HookType.TURN_PRE,
        hook_fn,
        priority=10,  # Run before lower-priority hooks
    )
    logger.info("context-scout enabled (model=%s)", scout_model)
