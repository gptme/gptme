"""Trust-on-first-use guard for project-level code execution and credentials.

Project ``gptme.toml`` files can configure ``context_cmd`` and
``[[hooks.scripts]]`` which both execute arbitrary shell commands with the user's
permissions. They can also spawn MCP stdio servers (``[mcp]``), load plugin
code (``[plugins] paths``), override environment such as provider base URLs
(``[env]``) and enable RAG (``[rag]``). A repository's
``.pre-commit-config.yaml`` likewise runs its hooks after every edit when
pre-commit checks auto-enable.  This is safe when the user wrote the config themselves, but unsafe
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
* Until approved, the non-shell surfaces are withheld from the loaded project
  config (see :func:`withhold_untrusted`), so no consumer can reach them.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
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


def _hash_content(content: dict) -> str:
    canonical = json.dumps(content, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


def compute_shell_hash(context_cmd: str | None, hook_commands: list[str]) -> str:
    """Return ``sha256:<hex>`` for the canonical shell content of a project config."""
    return _hash_content(_shell_content(context_cmd, hook_commands))


# Zero-width and bidi controls are invisible but reorder or hide what follows.
_CONTROL_CHARS = re.compile(
    "[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]"
)


def sanitize_for_prompt(text: str, *, allow_newlines: bool = False) -> str:
    """Make attacker-controlled text safe to show in a confirmation prompt.

    Control characters (ANSI escapes, carriage returns, bidi overrides) could
    redraw or hide part of the prompt, so the user would approve something other
    than what runs. Each one is replaced by a visible escape. ``allow_newlines``
    keeps ``\\n`` for multi-line text whose line structure the caller composed.
    """

    def _escape(match: re.Match[str]) -> str:
        code = ord(match.group())
        if allow_newlines and code == 0x0A:
            return "\n"
        return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"

    return _CONTROL_CHARS.sub(_escape, text)


def _sensitive_extras(project: ProjectConfig) -> dict:
    """Non-shell project settings that can run code or redirect credentials.

    Only non-empty surfaces are included, so a shell-only config keeps the hash
    it had before these surfaces were covered (existing approvals stay valid).
    """
    extras: dict = {}
    if project.mcp is not None:
        mcp = project.mcp.to_dict()
        if mcp["servers"] or project.mcp.enabled or project.mcp.auto_start:
            extras["mcp"] = mcp
    if project.env:
        extras["env"] = dict(project.env)
    if project.plugins.paths:
        extras["plugin_paths"] = list(project.plugins.paths)
    if project.rag.enabled:
        extras["rag"] = dataclasses.asdict(project.rag)
    return extras


def trust_source(project: ProjectConfig) -> ProjectConfig:
    """Return the full project config, even if its sensitive parts are withheld."""
    return project._untrusted_original or project


def project_trust_content(project: ProjectConfig) -> dict:
    """Canonical content a trust approval covers for *project*."""
    project = trust_source(project)
    content = _shell_content(*commands_from_project(project))
    content.update(_sensitive_extras(project))
    return content


def compute_project_hash(project: ProjectConfig) -> str:
    """Return ``sha256:<hex>`` over every trust-gated surface of *project*."""
    return _hash_content(project_trust_content(project))


def _needs_trust(content: dict) -> bool:
    return any(v for v in content.values())


def _project_lines(content: dict) -> list[str]:
    lines = []
    if content.get("context_cmd"):
        lines.append(f"  context_cmd: {content['context_cmd']}")
    lines.extend(f"  hook: {cmd}" for cmd in content.get("hooks", []))
    for server in content.get("mcp", {}).get("servers", []):
        if server.get("command"):
            argv = " ".join([server["command"], *map(str, server.get("args", []))])
            lines.append(f"  mcp server {server.get('name')!r} runs: {argv}")
        else:
            lines.append(
                f"  mcp server {server.get('name')!r} connects to: {server.get('url')}"
            )
    if mcp := content.get("mcp"):
        flags = {k: mcp[k] for k in ("enabled", "auto_start") if k in mcp}
        if flags:
            lines.append(f"  mcp settings: {flags}")
    lines.extend(
        f"  plugin path (loads code): {path}"
        for path in content.get("plugin_paths", [])
    )
    lines.extend(
        f"  env: {key}={value}" for key, value in content.get("env", {}).items()
    )
    if content.get("rag"):
        lines.append("  rag: enabled")
    return [sanitize_for_prompt(line) for line in lines]


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
    lines: list[str],
    workspace: Path | None,
    *,
    title: str = "Project wants to run shell commands",
    subtitle: str = "These come from a project-level gptme.toml",
) -> bool:
    """Show what the project wants to run and ask the user whether to trust it.

    Returns True if approved, False if denied.
    """
    from rich.console import Console
    from rich.markup import escape
    from rich.panel import Panel
    from rich.text import Text

    console = Console(stderr=True)

    # Plain Text: project-controlled strings must not be parsed as Rich markup,
    # and control characters must not redraw the prompt.
    body = Text(
        "\n".join(sanitize_for_prompt(line) for line in lines)
        if lines
        else "(no commands)"
    )

    ws_str = (
        f" in [bold]{escape(sanitize_for_prompt(str(workspace)))}[/bold]"
        if workspace
        else ""
    )
    console.print(
        Panel(
            body,
            title=f"[yellow]⚠ {title}{ws_str}[/yellow]",
            subtitle=f"[dim]{subtitle}[/dim]",
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


def _trust_all_env() -> bool:
    return os.environ.get(_ENV_TRUST_ALL, "").lower() in {"1", "true", "yes"}


def _is_approved(trust_hash: str, workspace: Path | None) -> bool:
    """Non-prompting check: env override, this session's decision, or the db."""
    if _trust_all_env():
        return True
    cache_key = (_workspace_key(workspace), trust_hash)
    if cache_key in _session_decisions:
        return _session_decisions[cache_key]
    if is_trusted(trust_hash, workspace):
        # Only approvals are cached here: a miss must not pre-empt the prompt.
        _session_decisions[cache_key] = True
        return True
    return False


def _check_trust(
    trust_hash: str,
    lines: list[str],
    workspace: Path | None,
    *,
    interactive: bool | None,
    what: str,
    **prompt_kwargs,
) -> bool:
    if _trust_all_env():
        logger.debug("Project trust bypassed via %s", _ENV_TRUST_ALL)
        return True

    cache_key = (_workspace_key(workspace), trust_hash)
    if cache_key in _session_decisions:
        return _session_decisions[cache_key]
    if _is_approved(trust_hash, workspace):
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
            "%s in %s has not been approved. Skipping it in non-interactive mode. "
            "Run gptme interactively once to approve, or set %s=1 to trust all.",
            what,
            workspace or "unknown workspace",
            _ENV_TRUST_ALL,
        )
        _session_decisions[cache_key] = False
        return False

    approved = _prompt_user(lines, workspace, **prompt_kwargs)
    _record(trust_hash, approved, workspace)
    _session_decisions[cache_key] = approved

    if not approved:
        logger.info("%s denied by user; it will not be used this session.", what)
    return approved


def check_project_shell_trust(
    context_cmd: str | None,
    hook_commands: list[str],
    workspace: Path | None,
    *,
    interactive: bool | None = None,
) -> bool:
    """Gate project-level shell execution behind a TOFU trust check.

    Prefer :func:`check_project_trust`, which also covers the non-shell
    surfaces. This covers only ``context_cmd`` and hook commands.

    Returns:
        True if the commands should be executed, False if they should be skipped.
    """
    if not context_cmd and not hook_commands:
        return True  # nothing to check
    content = _shell_content(context_cmd, hook_commands)
    return _check_trust(
        _hash_content(content),
        _project_lines(content),
        workspace,
        interactive=interactive,
        what="Project gptme.toml shell commands (context_cmd / hooks.scripts)",
    )


def check_project_trust(
    project: ProjectConfig,
    workspace: Path | None,
    *,
    interactive: bool | None = None,
) -> bool:
    """Gate every code-execution and credential surface of a project config.

    Covers ``context_cmd``, hook commands, MCP servers, plugin paths, ``[env]``
    and RAG enablement under one approval. May prompt.
    """
    content = project_trust_content(project)
    if not _needs_trust(content):
        return True
    return _check_trust(
        _hash_content(content),
        _project_lines(content),
        workspace,
        interactive=interactive,
        what="Project gptme.toml (shell commands, MCP servers, plugins, env, rag)",
        title="Project wants to run code or change settings",
    )


def is_project_trusted(project: ProjectConfig, workspace: Path | None) -> bool:
    """Non-prompting form of :func:`check_project_trust`."""
    content = project_trust_content(project)
    if not _needs_trust(content):
        return True
    return _is_approved(_hash_content(content), workspace)


def withhold_untrusted(project: ProjectConfig) -> ProjectConfig:
    """Return *project* with untrusted non-shell surfaces removed.

    ``context_cmd`` and hooks stay visible because their call sites run their
    own check (they also need the prompt). MCP servers, plugin paths, ``[env]``
    and RAG enablement are dropped until the project is approved, so readers of
    the loaded config (tool init, ``get_env``, lessons, ``/plugins``) never see
    them. The full config stays reachable via :func:`trust_source` for the prompt.
    """
    if not _sensitive_extras(project):
        return project
    if is_project_trusted(project, project._workspace):
        return project
    return dataclasses.replace(
        project,
        env={},
        mcp=None,
        plugins=dataclasses.replace(project.plugins, paths=[]),
        rag=dataclasses.replace(project.rag, enabled=False),
        _untrusted_original=project,
    )


def precommit_trust_hash(config_path: Path) -> str:
    """Hash of a pre-commit config, keyed on its location and content."""
    try:
        data = config_path.read_bytes()
    except OSError:
        data = b""
    return _hash_content(
        {
            "precommit_config": str(config_path.resolve()),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    )


def check_precommit_trust(
    config_path: Path,
    workspace: Path | None,
    *,
    interactive: bool | None = None,
    prompt: bool = True,
) -> bool:
    """Gate automatic pre-commit runs on a repository's hook config.

    Auto-enabled checks run every hook in *config_path* after file edits, so the
    config is approved like a project ``gptme.toml``: per workspace and per
    content hash. With ``prompt=False`` this only reports an existing approval.
    """
    trust_hash = precommit_trust_hash(config_path)
    if not prompt:
        return _is_approved(trust_hash, workspace)
    return _check_trust(
        trust_hash,
        [f"  pre-commit hooks from: {config_path}"],
        workspace,
        interactive=interactive,
        what=f"Pre-commit config {config_path}",
        title="Repository pre-commit hooks would run after every edit",
        subtitle="Approve to auto-run them; set GPTME_CHECK=false to never run them",
    )
