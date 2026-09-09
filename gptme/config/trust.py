"""Trust-on-first-use guard for project-level shell execution.

Project ``gptme.toml`` files can configure ``context_cmd`` and
``[[hooks.scripts]]`` which both execute arbitrary shell commands with the user's
permissions.  This is safe when the user wrote the config themselves, but unsafe
when they ``git clone`` a repository containing a pre-crafted ``gptme.toml`` —
the same class of risk as a malicious ``package.json`` ``postinstall`` script.

This module implements a **trust-on-first-use** (TOFU) gate:

* On first encounter the user is shown the exact commands and asked to approve.
* Approval is stored keyed by **(workspace, SHA-256 of the command set)** so
  the same relative command in a different project is re-prompted.
* If the ``gptme.toml`` is modified (different hash), the prompt fires again.
* In non-interactive environments the gate defaults to **deny**.
* User-global config (``~/.config/gptme/gptme.toml``) is implicitly trusted —
  the guard only applies to project configs found in workspace directories.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .models import ProjectConfig

logger = logging.getLogger(__name__)

# env var that bypasses the prompt (useful for CI / ``--no-confirm`` equivalents)
_ENV_TRUST_ALL = "GPTME_TRUST_PROJECT_SHELL"

# In-process decisions so init() and prompt construction share one answer.
_session_decisions: dict[tuple[str, str], bool] = {}


def _workspace_key(workspace: Path | None) -> str:
    """Stable absolute path string for a workspace, or ``""`` if unknown."""
    if workspace is None:
        return ""
    try:
        return str(workspace.resolve())
    except OSError:
        return str(workspace)


def _shell_content(context_cmd: str | None, hook_commands: list[str]) -> dict:
    """Return a canonical dict of the shell-only parts of a project config."""
    return {"context_cmd": context_cmd, "hooks": sorted(hook_commands)}


def compute_shell_hash(context_cmd: str | None, hook_commands: list[str]) -> str:
    """Return ``sha256:<hex>`` for the canonical shell content of a project config."""
    canonical = json.dumps(_shell_content(context_cmd, hook_commands), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


def commands_from_project(project: ProjectConfig) -> tuple[str | None, list[str]]:
    """Return ``(context_cmd, hook_commands)`` for a project config.

    Both trust-check call sites must use this so they hash the same command set.
    """
    hook_commands = [h.command for h in project.hooks.scripts]
    return project.context_cmd, hook_commands


def _trust_db_path() -> Path:
    from ..dirs import get_config_dir

    return get_config_dir() / "project-trust.toml"


def _load_db() -> dict:
    import tomlkit

    p = _trust_db_path()
    if not p.exists():
        return {}
    try:
        return tomlkit.loads(p.read_text()).unwrap()
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not read project trust db %s: %s", p, exc)
        return {}


def _save_db(db: dict) -> None:
    import tomlkit

    p = _trust_db_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tomlkit.dumps(db))
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not write project trust db %s: %s", p, exc)


def is_trusted(shell_hash: str, workspace: Path | None) -> bool:
    """Return True iff *shell_hash* is approved for *workspace*."""
    db = _load_db()
    ws_key = _workspace_key(workspace)
    entry = db.get("workspaces", {}).get(ws_key, {}).get(shell_hash, {})
    return bool(entry.get("approved"))


def _record(shell_hash: str, approved: bool, workspace: Path | None) -> None:
    import datetime

    db = _load_db()
    workspaces = db.setdefault("workspaces", {})
    entries = workspaces.setdefault(_workspace_key(workspace), {})
    entries[shell_hash] = {
        "approved": approved,
        "at": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
    }
    _save_db(db)


def _prompt_user(
    context_cmd: str | None,
    hook_commands: list[str],
    workspace: Path | None,
) -> bool:
    """Show commands and ask the user whether to trust them.

    Returns True if approved, False if denied.
    """
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.text import Text

    console = Console(stderr=True)

    lines = []
    if context_cmd:
        lines.append(f"  context_cmd: {context_cmd}")
    lines.extend(f"  hook: {cmd}" for cmd in hook_commands)

    # Plain Text: project-controlled strings must not be parsed as Rich markup.
    body = Text("\n".join(lines) if lines else "(no commands)")

    ws_str = f" in [bold]{escape(str(workspace))}[/bold]" if workspace else ""
    console.print(
        Panel(
            body,
            title=f"[yellow]⚠ Project wants to run shell commands{ws_str}[/yellow]",
            subtitle="[dim]These come from a project-level gptme.toml[/dim]",
            border_style="yellow",
        )
    )
    console.print(
        "[bold]Trust and run these commands?[/bold] "
        "([green]y[/green]es / [red]N[/red]o, stored per workspace): ",
        end="",
    )

    try:
        answer = input().strip().lower()
    except (EOFError, OSError):
        # EOF: piped /dev/null. OSError: pytest capturing stdin (DontReadFromInput).
        answer = ""

    return answer in {"y", "yes"}


def check_project_shell_trust(
    context_cmd: str | None,
    hook_commands: list[str],
    workspace: Path | None,
    *,
    interactive: bool | None = None,
) -> bool:
    """Gate project-level shell execution behind a TOFU trust check.

    Args:
        context_cmd: The ``context_cmd`` string from the project config, or None.
        hook_commands: List of shell command strings from ``hooks.scripts``.
        workspace: Path to the project workspace (used as the trust scope).
        interactive: Whether we are in an interactive session.  If None, auto-
            detected from ``sys.stdin.isatty()``.

    Returns:
        True if the commands should be executed, False if they should be skipped.
    """
    import os

    if not context_cmd and not hook_commands:
        return True  # nothing to check

    # Trust-all env override (non-interactive CI, ``--no-confirm`` equivalents)
    if os.environ.get(_ENV_TRUST_ALL, "").lower() in {"1", "true", "yes"}:
        logger.debug("Project shell trust bypassed via %s", _ENV_TRUST_ALL)
        return True

    shell_hash = compute_shell_hash(context_cmd, hook_commands)
    cache_key = (_workspace_key(workspace), shell_hash)
    if cache_key in _session_decisions:
        return _session_decisions[cache_key]

    # Already approved for this workspace?
    if is_trusted(shell_hash, workspace):
        return True

    # Determine interactivity. ``interactive=True`` is not enough on its own:
    # get_prompt() defaults to True, pytest captures stdin, and piped
    # invocations cannot actually prompt. Require a real TTY before calling
    # input().
    if interactive is None:
        interactive = sys.stdin.isatty()
    can_prompt = bool(interactive) and sys.stdin.isatty()

    if not can_prompt:
        logger.warning(
            "Project gptme.toml in %s contains shell commands (context_cmd / hooks.scripts) "
            "that have not been approved. Skipping them in non-interactive mode. "
            "Run gptme interactively once to approve, or set %s=1 to trust all.",
            workspace or "unknown workspace",
            _ENV_TRUST_ALL,
        )
        _session_decisions[cache_key] = False
        return False

    approved = _prompt_user(context_cmd, hook_commands, workspace)
    _record(shell_hash, approved, workspace)
    _session_decisions[cache_key] = approved

    if not approved:
        logger.info(
            "Project shell commands denied by user; they will not run this session."
        )
    return approved
