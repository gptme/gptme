"""Memory root resolution: an ordered list of layers, nearest first.

Order (see gptme/gptme#3734, "layers, not a third store"):

1. ``explicit`` — ``GPTME_MEMORY_DIRS`` (colon-separated); when set it replaces
   every other layer.
2. ``project`` — ``<workspace>/memory/`` when it exists.
3. ``cc`` — Claude Code's ``~/.claude/projects/<hash>/memory/``. Always
   present so CC-native memories are never invisible; written only when it is
   the only writable layer.
4. ``agent`` — ``$GPTME_AGENT_WORKSPACE/memory/`` when set: an agent's brain
   while it works in another repository.
5. ``user`` — ``<config dir>/memory/``, shared across every workspace.

Roots that resolve to the same directory are collapsed onto the first scope
that named them (for an agent working in its own brain, ``project`` and ``cc``
are one directory).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from ..dirs import get_cc_memory_dir, get_config_dir, get_workspace

ENV_DIRS = "GPTME_MEMORY_DIRS"
ENV_AGENT_WORKSPACE = "GPTME_AGENT_WORKSPACE"
SCOPES = ("explicit", "project", "cc", "agent", "user")


@dataclass(frozen=True)
class MemoryRoot:
    scope: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def resolve_roots(
    workspace: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> list[MemoryRoot]:
    env = os.environ if env is None else env
    explicit = env.get(ENV_DIRS, "").strip()
    if explicit:
        return _dedupe(
            MemoryRoot("explicit", Path(p).expanduser())
            for p in explicit.split(os.pathsep)
            if p.strip()
        )

    ws = (workspace or get_workspace()).resolve()
    candidates: list[MemoryRoot] = []
    project = ws / "memory"
    if project.is_dir():
        candidates.append(MemoryRoot("project", project))
    candidates.append(MemoryRoot("cc", get_cc_memory_dir(ws)))
    agent_ws = env.get(ENV_AGENT_WORKSPACE, "").strip()
    if agent_ws:
        agent = Path(agent_ws).expanduser() / "memory"
        if agent.is_dir():
            candidates.append(MemoryRoot("agent", agent))
    candidates.append(MemoryRoot("user", (config_dir or get_config_dir()) / "memory"))
    return _dedupe(candidates)


def default_write_root(roots: list[MemoryRoot]) -> MemoryRoot:
    """The scope ``save`` targets when none is given: explicit or project, else cc."""
    for scope in ("explicit", "project", "cc"):
        for r in roots:
            if r.scope == scope:
                return r
    if roots:
        return roots[0]
    raise KeyError("no memory roots resolved")


def _dedupe(roots) -> list[MemoryRoot]:
    out: list[MemoryRoot] = []
    seen: set[Path] = set()
    for r in roots:
        key = r.path.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
