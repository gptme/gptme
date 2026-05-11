"""Auto-snapshot hook for opt-in workspace rollback.

Wires :mod:`gptme.workspace_snapshot` into ``tool.execute.pre`` and
``tool.execute.post`` so write-capable tool calls record reversible
pre/post snapshots. See ``knowledge/technical-designs/workspace-rollback-auto-snapshots.md``
in the Bob repo for the full design.

Activation
----------
The hook is **opt-in**. It self-no-ops unless either:

- ``GPTME_AUTO_SNAPSHOTS`` is set to a truthy value, or
- The CLI passed ``--auto-snapshots`` (which sets the same env var).

Storage backend: ``$XDG_STATE_HOME/gptme/workspace-snapshots/<fingerprint>.git``
(an XDG-located shadow git repo, not a ``.gptme-snapshots/`` directory inside
the user's workspace).

Mutating-tool policy
--------------------
Always-mutating tools snapshot unconditionally::

    save, append, patch, morph

Conditionally mutating tools snapshot only when their payload matches a
conservative "obvious mutator" classifier::

    shell, tmux

Read-only or unclassified shell payloads are skipped. False negatives
(missed snapshots) are preferred over false positives (snapshotting every
``ls`` in a big repo).
"""

from __future__ import annotations

import logging
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..hooks import HookType, register_hook
from ..workspace_snapshot import (
    DEFAULT_MAX_SNAPSHOTS,
    Shadow,
    init_shadow,
    prune,
    snapshot,
    tree_hash,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..hooks import StopPropagation
    from ..logmanager import Log
    from ..message import Message

logger = logging.getLogger(__name__)

# ContextVar so pre and post halves of one tool call share state.
_pre_tree_var: ContextVar[str | None] = ContextVar(
    "auto_snapshot_pre_tree", default=None
)

ALWAYS_MUTATING: frozenset[str] = frozenset({"save", "append", "patch", "morph"})
CONDITIONALLY_MUTATING: frozenset[str] = frozenset({"shell", "tmux", "ipython"})

# ipython is included because it can call open() / subprocess just like shell.
# Treat any ipython payload as conditionally mutating via the same classifier.

# Patterns that indicate a shell payload mutates the workspace.
# Conservative — prefer false negatives over false positives.
_SHELL_MUTATOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Output redirection (anywhere on the line).
    re.compile(r"(?:^|[^|&])(?:>>?|1>|2>|&>)"),
    # tee writes to a file.
    re.compile(r"\btee\b"),
    # Heredoc/herestring writes.
    re.compile(r"<<-?'?\"?\w+"),
    # In-place editors.
    re.compile(r"\bsed\s+[^|]*-i\b"),
    re.compile(r"\bperl\s+[^|]*-p?i\b"),
    re.compile(r"\bawk\s+[^|]*-i\s+inplace\b"),
    # Filesystem mutators.
    re.compile(r"\b(?:touch|mkdir|rmdir|rm|mv|cp|ln|chmod|chown)\b"),
    # Tar/zip extracting into workspace.
    re.compile(r"\btar\s+[^|]*-x"),
    re.compile(r"\bunzip\b"),
    # VCS mutators that modify the working tree.
    re.compile(
        r"\bgit\s+(?:apply|restore|checkout|clean|reset|pull|merge|rebase|stash\s+pop)\b"
    ),
    # Common build/install/test tools that write into the workspace.
    re.compile(r"\b(?:make|cmake|cargo|npm|yarn|pnpm|pip|uv|poetry)\b"),
    # Python/shell test runners that may write reports.
    re.compile(r"\bpytest\b"),
)


def is_mutating_shell_payload(content: str | None) -> bool:
    """Return True if ``content`` looks like a workspace-mutating shell payload.

    Conservative: only positive signals trigger. Unknown / plain reads return
    False. See module docstring for the contract.
    """
    if not content:
        return False
    text = content.strip()
    if not text:
        return False
    return any(pat.search(text) for pat in _SHELL_MUTATOR_PATTERNS)


def is_mutating_tmux_payload(content: str | None) -> bool:
    """Return True if a tmux invocation runs a mutating shell payload.

    Handles statically visible cases only::

        new-session ... '<cmd>'
        split-window ... '<cmd>'
        respawn-pane ... '<cmd>'

    ``send-keys`` and pure pane manipulation are intentionally treated as
    non-mutating in v1.
    """
    if not content:
        return False
    text = content.strip()
    m = re.search(
        r"\b(?:new-session|split-window|respawn-pane)\b[^']*'([^']*)'",
        text,
    )
    if m:
        return is_mutating_shell_payload(m.group(1))
    m = re.search(
        r"\b(?:new-session|split-window|respawn-pane)\b[^\"]*\"([^\"]*)\"",
        text,
    )
    if m:
        return is_mutating_shell_payload(m.group(1))
    return False


def classify_tool_use(tool_name: str, content: str | None) -> bool:
    """Decide whether ``tool_name(content)`` should trigger a snapshot."""
    if tool_name in ALWAYS_MUTATING:
        return True
    if tool_name in CONDITIONALLY_MUTATING:
        if tool_name == "tmux":
            return is_mutating_tmux_payload(content)
        return is_mutating_shell_payload(content)
    return False


def _enabled() -> bool:
    return os.environ.get("GPTME_AUTO_SNAPSHOTS", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _tool_payload(tool_use: Any) -> str | None:
    """Return the classifier payload for a tool call across tool formats.

    Markdown/XML tool calls populate ``content`` directly. Structured ``tool``
    format stores the payload in ``kwargs`` instead, for example:

    - ``shell`` / ``tmux``: ``{"command": ...}``
    - ``ipython``: ``{"code": ...}``
    """
    content = getattr(tool_use, "content", None)
    if isinstance(content, str) and content.strip():
        return content

    kwargs = getattr(tool_use, "kwargs", None)
    if not isinstance(kwargs, dict):
        return content if isinstance(content, str) else None

    tool_name = getattr(tool_use, "tool", None) or ""
    if tool_name in ("shell", "tmux"):
        payload = kwargs.get("command")
    elif tool_name == "ipython":
        payload = kwargs.get("code")
    else:
        payload = None

    if isinstance(payload, str) and payload:
        return payload
    return content if isinstance(content, str) else None


def _max_snapshots() -> int:
    raw = os.environ.get("GPTME_AUTO_SNAPSHOT_MAX")
    if not raw:
        return DEFAULT_MAX_SNAPSHOTS
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_MAX_SNAPSHOTS
    return max(1, val)


def _shadow_for(workspace: Path | None) -> Shadow | None:
    if workspace is None:
        return None
    try:
        return init_shadow(Path(workspace))
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("auto-snapshot init failed: %s", e)
        return None


def _pre(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Capture pre-tool snapshot if the tool is mutating."""
    if not _enabled():
        return
    if tool_use is None:
        return
    tool_name = getattr(tool_use, "tool", None) or ""
    content = _tool_payload(tool_use)
    if not classify_tool_use(tool_name, content):
        _pre_tree_var.set(None)
        return
    shadow = _shadow_for(workspace)
    if shadow is None:
        return
    try:
        _pre_tree_var.set(
            None
        )  # reset before work; exception paths must not leak stale hash
        shadow.run("add", "-A")
        before = tree_hash(shadow, stage=False)
        snapshot(shadow, label=f"pre:{tool_name}", stage=False)
        _pre_tree_var.set(before)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("auto-snapshot pre failed: %s", e)
    return
    yield  # noqa: unreachable — presence of `yield` makes this a generator; matches cwd_changed.py shape


def _post(
    log: Log, workspace: Path | None, tool_use: Any
) -> Generator[Message | StopPropagation, None, None]:
    """Emit post-tool snapshot only when the workspace tree actually changed."""
    if not _enabled():
        return
    if tool_use is None:
        return
    tool_name = getattr(tool_use, "tool", None) or ""
    content = _tool_payload(tool_use)
    if not classify_tool_use(tool_name, content):
        return
    shadow = _shadow_for(workspace)
    if shadow is None:
        return
    try:
        before = _pre_tree_var.get()
        shadow.run("add", "-A")
        after = tree_hash(shadow, stage=False)
        if before is not None and after is not None and before == after:
            # No mutation actually happened; skip noise.
            return
        snapshot(shadow, label=f"post:{tool_name}", stage=False)
        prune(shadow, keep=_max_snapshots())
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("auto-snapshot post failed: %s", e)
    return
    yield  # noqa: unreachable — presence of `yield` makes this a generator; matches cwd_changed.py shape


def register() -> None:
    """Register pre/post auto-snapshot hooks.

    Always registered; the hooks self-no-op when ``GPTME_AUTO_SNAPSHOTS``
    is unset. Registering unconditionally keeps the activation surface a
    single env var and avoids ordering issues with plugin loaders.
    """
    register_hook(
        "auto_snapshots.pre",
        HookType.TOOL_EXECUTE_PRE,
        _pre,
        priority=90,  # After cwd_changed.store (100) but before user hooks
    )
    register_hook(
        "auto_snapshots.post",
        HookType.TOOL_EXECUTE_POST,
        _post,
        priority=90,
    )
    logger.debug("Registered auto-snapshot hooks")
