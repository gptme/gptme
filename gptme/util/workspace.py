"""Context-local working directory resolution, without importing tool implementations.

The initial workspace enables server isolation; the persistent shell's live cwd
wins after ``cd``. A promoted command leaves a seed while its replacement shell
is created lazily. With no session workspace, CLI callers use the real OS cwd.
This is path resolution, not a virtual filesystem or a security sandbox: raw
relative I/O still uses the process cwd.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ..tools.shell import ShellSession


_shell_var: ContextVar[ShellSession | None] = ContextVar("shell", default=None)
_workspace_cwd: ContextVar[str | None] = ContextVar("workspace_cwd", default=None)
# Explicit lookup scope takes precedence without changing the shell's cwd/seed.
_workspace_override: ContextVar[str | None] = ContextVar(
    "workspace_override", default=None
)
# One-shot seed, distinct from the initial workspace / server skip-chdir flag.
_promoted_next_cwd: ContextVar[str | None] = ContextVar(
    "promoted_next_cwd", default=None
)


def set_workspace_cwd(cwd: str) -> None:
    """Set the initial session workspace before creating its shell."""
    _workspace_cwd.set(cwd)


def get_workspace_cwd() -> str | None:
    """Return the session cwd, following its shell; None means CLI fallback."""
    if override := _workspace_override.get():
        return override
    workspace = _workspace_cwd.get()
    shell = _shell_var.get()
    if workspace is not None:
        if shell is not None and shell._cwd:
            return shell._cwd
        return _promoted_next_cwd.get() or workspace
    return workspace


def current_workspace() -> Path:
    """The directory relative paths resolve against in the current context."""
    workspace = get_workspace_cwd()
    return Path(workspace).resolve() if workspace else Path.cwd().resolve()


@contextmanager
def workspace_scope(workspace: Path | str | None) -> Iterator[None]:
    """Anchor :func:`current_workspace` at *workspace* for the block.

    For code that runs before the session sets its workspace, such as system
    prompt assembly. ``None`` leaves the current resolution unchanged.
    """
    if workspace is None:
        yield
        return
    token = _workspace_override.set(str(Path(workspace).resolve()))
    try:
        yield
    finally:
        _workspace_override.reset(token)


def resolve_in_workspace(path: Path) -> Path:
    """Expand and resolve a path, anchoring relative paths at the session cwd."""
    path = path.expanduser()
    if not path.is_absolute():
        path = current_workspace() / path
    return path.resolve()
