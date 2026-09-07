"""
Root resolution for the memory store.

Roots are resolved in priority order; reads union all, writes go to the
first writeable root (or --scope override).

Resolution order:
  1. GPTME_MEMORY_DIRS  (explicit, colon-separated)
  2. project: <workspace>/memory/
  3. cc:      ~/.claude/projects/<hash>/memory/  (always read)
  4. user:    ~/.config/gptme/memory/
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..dirs import get_cc_memory_dir, get_config_dir, get_workspace

ScopeType = Literal["project", "cc", "user", "env"]


def resolve_roots(workspace: Path | None = None) -> list[Path]:
    """Return all memory roots that should be read, in priority order.

    Roots that don't exist yet are included so callers can decide whether to
    create them (e.g. on write). Duplicates are removed, preserving order.
    """
    if workspace is None:
        workspace = get_workspace()

    roots: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        rp = p.resolve() if p.exists() else p
        if rp not in seen:
            seen.add(rp)
            roots.append(p)

    # 1. Explicit env override (colon-separated list)
    env_dirs = os.environ.get("GPTME_MEMORY_DIRS", "")
    for d in env_dirs.split(":"):
        d = d.strip()
        if d:
            _add(Path(d))

    # 2. Project-local memory/
    _add(workspace / "memory")

    # 3. CC per-project memory dir (always readable)
    _add(get_cc_memory_dir(workspace))

    # 4. User-level memory
    _add(get_config_dir() / "memory")

    return roots


def write_root(scope: ScopeType | None = None, workspace: Path | None = None) -> Path:
    """Return the root directory to write new entries to.

    Args:
        scope: Explicit target scope. None → first existing root, or project.
        workspace: Override workspace root.
    """
    if workspace is None:
        workspace = get_workspace()

    if scope == "cc":
        return get_cc_memory_dir(workspace)
    if scope == "user":
        return get_config_dir() / "memory"
    if scope == "project":
        return workspace / "memory"
    if scope == "env":
        env_dirs = os.environ.get("GPTME_MEMORY_DIRS", "")
        for d in env_dirs.split(":"):
            d = d.strip()
            if d:
                return Path(d)
        raise ValueError("GPTME_MEMORY_DIRS is not set; cannot use scope 'env'")

    # Default: CC memory dir (compatible with CC's own memory tool)
    return get_cc_memory_dir(workspace)
