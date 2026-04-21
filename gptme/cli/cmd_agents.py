"""CLI commands for live agent scanning.

Provides ``gptme-util agents`` subcommands for inspecting other agent processes
running on the same host.

Subcommands:
- ``scan``: List live agent processes (all runtimes, any workspace)
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import click


@click.group()
def agents() -> None:
    """Inspect live agent processes on this host."""


@agents.command("scan")
@click.option(
    "--workspace",
    "-w",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Only show agents whose CWD is under this path. "
    "By default all agents on the host are shown.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Include stale/stuck agents (excluded by default).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON.",
)
def scan(
    workspace: Path | None,
    show_all: bool,
    as_json: bool,
) -> None:
    """List live agent processes (gptme, claude-code, codex, aider, …).

    By default shows all active (non-stale) agents on the host.
    Use --workspace to filter by repo root.
    Use --all to also include stale/stuck processes.

    Exit code: 0 if at least one active agent found, 1 if none.
    """
    from ..hooks.workspace_agents import (
        _format_agent_line,
        _format_duration,
        scan_agents,
    )

    results = scan_agents(workspace=str(workspace) if workspace else None)

    active = [a for a in results if not a.stale]
    stale = [a for a in results if a.stale]

    shown = results if show_all else active

    if as_json:
        output = []
        for a in shown:
            d = asdict(a)
            output.append(d)
        json.dump(output, sys.stdout, indent=2)
        print()
    else:
        if not shown:
            label = "agents" if show_all else "active agents"
            scope = f" in {workspace}" if workspace else ""
            click.echo(f"No {label} found{scope}.")
        else:
            scope = f" in {workspace}" if workspace else " on this host"
            click.echo(f"Live agents{scope}:")
            for a in shown:
                click.echo(f"  {_format_agent_line(a)}")

        if not show_all and stale:
            n = len(stale)
            ages = ", ".join(
                _format_duration(a.uptime_seconds)
                for a in stale
                if a.uptime_seconds is not None
            )
            click.echo(
                f"  ({n} stale process{'es' if n != 1 else ''} hidden"
                + (f"; ages: {ages}" if ages else "")
                + "; use --all to show)",
                err=False,
            )

    sys.exit(0 if active else 1)
