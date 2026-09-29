"""Inject the working-tree diff at session start when it is dirty.

Narrower successor to the rejected gptme/gptme#4009 (`--diff`/`--diff-ref` CLI
flags + suggestion-acceptance ledger). Erik's guidance on rejecting it: "This
would maybe be better done as a hook or plugin or something." This hook does
exactly that — no new CLI surface, no ledger, no line-range annotation. It
just yields `git diff HEAD` as a hidden system message when the workspace has
uncommitted changes, so a review/continuation session doesn't need to be told
to go look.

CLI SESSION_START runs before the first user prompt is appended, so this hook
also registers on TURN_PRE (after the prompt is in the log), matching the
pattern in `knowledge_inject.py`. Injection is once per conversation: later
turns no-op if a hidden system message already contains this hook's sentinel
as a line prefix.
"""

import logging
import subprocess
from collections.abc import Generator
from pathlib import Path
from typing import Any

from ..hooks import HookType, StopPropagation, register_hook
from ..message import Message
from ..util.git_cmd import git_inspect_cmd

logger = logging.getLogger(__name__)

# Prefix of this hook's hidden system message. Dedup requires this sentinel as
# a line prefix so a quoted "working tree has uncommitted changes" phrase in
# unrelated content cannot suppress injection.
_INJECT_SENTINEL = "<!-- gptme-dirty-diff -->"

# Cap so a large dirty tree doesn't blow the context budget at session start.
_MAX_DIFF_CHARS = 16_000

_DIFF_TIMEOUT_SECONDS = 10


def _content_has_inject_sentinel(content: str) -> bool:
    return any(
        line.lstrip().startswith(_INJECT_SENTINEL) for line in content.splitlines()
    )


def _already_injected(msgs: list[Message]) -> bool:
    for msg in msgs:
        if getattr(msg, "role", None) != "system":
            continue
        if not getattr(msg, "hide", False):
            continue
        content = getattr(msg, "content", None)
        if isinstance(content, str) and _content_has_inject_sentinel(content):
            return True
    return False


def _messages_from_context(
    initial_msgs: list[Message] | None,
    manager: Any,
) -> list[Message]:
    if manager is not None:
        log = getattr(manager, "log", manager)
        if isinstance(log, list):
            if log:
                return list(log)
        else:
            msgs = getattr(log, "messages", None)
            if msgs:
                return list(msgs)
    return list(initial_msgs or [])


def _get_dirty_diff(workspace: Path) -> str | None:
    """Return `git diff HEAD` for `workspace`, or None if clean/not a repo."""
    try:
        result = subprocess.run(
            [
                *git_inspect_cmd(),
                "-C",
                str(workspace),
                "diff",
                # git_inspect_cmd() sets diff.external="" to suppress a
                # configured external diff tool, but an *empty* diff.external
                # is not equivalent to unset: git still tries to exec it and
                # fails ("cannot run : No such file or directory"). --no-ext-diff
                # is the flag that actually disables it for content diffs.
                "--no-ext-diff",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=_DIFF_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("dirty diff: git diff failed for %s: %s", workspace, e)
        return None
    if result.returncode != 0:
        # Not a git repo, or HEAD doesn't exist yet (empty repo) — skip quietly.
        return None
    diff = result.stdout
    return diff if diff.strip() else None


def inject_dirty_diff(
    logdir: Path | None = None,
    workspace: Path | None = None,
    initial_msgs: list[Message] | None = None,
    manager: Any = None,
    **kwargs: Any,
) -> Generator[Message | StopPropagation, None, None]:
    """Yield a hidden system message with the dirty working-tree diff.

    No-ops when there's no workspace, the tree is clean or not a git repo, or
    this conversation already received the diff. Failures never block session
    start.
    """
    try:
        if manager is not None and workspace is None:
            workspace = getattr(manager, "workspace", None)
        if workspace is None:
            return
        msgs = _messages_from_context(initial_msgs, manager)
        if _already_injected(msgs):
            return
        diff = _get_dirty_diff(Path(workspace))
        if not diff:
            return
        truncated = len(diff) > _MAX_DIFF_CHARS
        if truncated:
            diff = diff[:_MAX_DIFF_CHARS]
        note = " (truncated)" if truncated else ""
        body = (
            f"The working tree has uncommitted changes. `git diff HEAD`{note}:\n\n"
            f"```diff\n{diff}\n```"
        )
        logger.debug("Injecting dirty-tree diff for workspace %s", workspace)
        yield Message("system", f"{_INJECT_SENTINEL}\n{body}", hide=True)
    except Exception:
        logger.debug("dirty diff inject skipped", exc_info=True)
        return


def register() -> None:
    register_hook(
        "dirty_diff.session_start",
        HookType.SESSION_START,
        inject_dirty_diff,
        priority=0,
    )
    # CLI SESSION_START does not include prompt_msgs; TURN_PRE does.
    register_hook(
        "dirty_diff.turn_pre",
        HookType.TURN_PRE,
        inject_dirty_diff,
        priority=0,
    )
