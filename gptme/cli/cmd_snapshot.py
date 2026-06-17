"""CLI commands for workspace snapshot management (``gptme-util snapshot``)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import click


@click.group()
def snapshot():
    """Commands for managing workspace snapshots."""


@snapshot.command("list")
@click.option(
    "-n",
    "--limit",
    default=20,
    type=click.IntRange(min=1),
    help="Maximum number of snapshots to show.",
)
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output as JSON array.",
)
@click.option(
    "--workspace",
    "-w",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help="Workspace directory (defaults to current directory).",
)
def list_cmd(limit: int, output_json: bool, workspace: str | None) -> None:
    """List workspace snapshots, newest first.

    Reads snapshots from the shadow git repo for the given workspace.
    Prints a table with SHA, timestamp, message count, and label.
    """
    from ..workspace_snapshot import Shadow, list_snapshots_rich

    ws = Path(workspace) if workspace else Path.cwd()
    shadow = Shadow.for_workspace(ws)
    entries = list_snapshots_rich(shadow, limit=limit)

    if output_json:
        click.echo(json.dumps(entries, indent=2))
        return

    if not entries:
        return

    click.echo(f"{'SHA':<9}  {'Date':<16}  {'Msgs':>4}  Label")
    click.echo("-" * 58)
    for e in entries:
        ts = e["timestamp"]
        dt = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if ts is not None
            else "—" * 16
        )
        msgs = str(e["n_msgs"]) if e["n_msgs"] is not None else "—"
        click.echo(f"{e['sha']:<9}  {dt:<16}  {msgs:>4}  {e['label']}")
