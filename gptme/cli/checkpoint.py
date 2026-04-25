"""``gptme-checkpoint`` CLI for Git-backed workspace recovery.

Top-level entry point that wraps :mod:`gptme.checkpoint`. See the module
docstring there for the design constraints (clean_git first, XDG-backed
ledger, conservative restore semantics).
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from ..checkpoint import (
    CheckpointError,
    classify,
    create_checkpoint,
    diff_checkpoint,
    list_checkpoints,
    restore_checkpoint,
)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Lightweight session-level recovery markers for Git workspaces.

    Records the current ``HEAD`` so an agent run that touches more than
    expected can be rewound with ``gptme-checkpoint restore``. Not a
    replacement for ``git`` — see ``gptme-checkpoint create --help``.
    """


@main.command("create")
@click.argument(
    "path", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--include-dirty",
    is_flag=True,
    help="Allow checkpointing a dirty git workspace (records HEAD only).",
)
@click.option("--session-id", default=None, help="Custom session identifier.")
def cmd_create(
    path: Path | None,
    include_dirty: bool,
    session_id: str | None,
) -> None:
    """Record a checkpoint at the current HEAD."""
    target = path or Path.cwd()
    try:
        record, existing_sha = create_checkpoint(
            target,
            session_id=session_id,
            include_dirty=include_dirty,
        )
    except CheckpointError as exc:
        click.echo(f"checkpoint: {exc}", err=True)
        sys.exit(1)

    if record is None:
        head = existing_sha[:12] if existing_sha else "?"
        click.echo(f"Already checkpointed at {head} — nothing to do.")
    else:
        click.echo(
            f"Checkpoint created: {record.head_sha[:12]} (session={record.session_id})"
        )


@main.command("list")
@click.argument(
    "path", required=False, type=click.Path(file_okay=False, path_type=Path)
)
def cmd_list(path: Path | None) -> None:
    """List recorded checkpoints for the workspace."""
    target = path or Path.cwd()
    decision = classify(target)
    if decision.repo_root is None:
        click.echo(f"checkpoint: {decision.reason}", err=True)
        sys.exit(1)

    records = list_checkpoints(decision.repo_root)
    if not records:
        click.echo("No checkpoints yet.")
        return

    current_sha = decision.head_sha or ""
    click.echo(f"{'#':>3}  {'Session':<14}  {'Timestamp':<20}  {'HEAD':<12}  Workspace")
    for i, r in enumerate(records, start=1):
        marker = " *" if r.head_sha == current_sha else ""
        ts = r.timestamp[:19].replace("T", " ")
        click.echo(
            f"{i:>3}  {r.session_id:<14}  {ts:<20}  {r.head_sha[:12]:<12}  "
            f"{r.workspace}{marker}"
        )


@main.command("diff")
@click.argument("identifier")
@click.argument(
    "path", required=False, type=click.Path(file_okay=False, path_type=Path)
)
def cmd_diff(identifier: str, path: Path | None) -> None:
    """Diff current state against a checkpoint."""
    target = path or Path.cwd()
    try:
        output = diff_checkpoint(target, identifier)
    except CheckpointError as exc:
        click.echo(f"checkpoint: {exc}", err=True)
        sys.exit(1)

    if output:
        click.echo(output, nl=False)
    else:
        click.echo("No changes since checkpoint.")


@main.command("restore")
@click.argument("identifier")
@click.argument(
    "path", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--include-dirty",
    is_flag=True,
    help="Allow restore in a dirty workspace (DISCARDS uncommitted changes).",
)
def cmd_restore(identifier: str, path: Path | None, include_dirty: bool) -> None:
    """Restore working tree to a checkpoint HEAD via ``git reset --hard``."""
    target = path or Path.cwd()
    try:
        result = restore_checkpoint(target, identifier, include_dirty=include_dirty)
    except CheckpointError as exc:
        click.echo(f"checkpoint: {exc}", err=True)
        sys.exit(1)

    click.echo(result)


if __name__ == "__main__":  # pragma: no cover
    main()
