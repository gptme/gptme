"""
Checkpoint commands for workspace recovery.
"""

from __future__ import annotations

from pathlib import Path

from ..checkpoint import (
    CheckpointError,
    classify,
    create_checkpoint,
    diff_checkpoint,
    list_checkpoints,
    restore_checkpoint,
)
from .base import CommandContext, command


def _print_usage() -> None:
    print("Usage: /checkpoint <create|list|diff|restore> ...")
    print()
    print("Commands:")
    print("  create [--include-dirty] [--session-id ID]")
    print("      Record a checkpoint at the current workspace HEAD.")
    print("  list")
    print("      List recorded checkpoints for the current workspace.")
    print("  diff <identifier>")
    print("      Diff current state against a checkpoint.")
    print("  restore <identifier> [--include-dirty]")
    print("      Restore the workspace to a checkpoint.")


def _workspace_path(ctx: CommandContext) -> Path | None:
    workspace = getattr(ctx.manager, "workspace", None)
    if workspace is None:
        print("checkpoint: no workspace configured for this session")
        return None
    return Path(workspace)


@command("checkpoint")
def cmd_checkpoint(ctx: CommandContext) -> None:
    """Manage workspace checkpoints."""
    target = _workspace_path(ctx)
    if target is None:
        return

    if not ctx.args or ctx.args[0] in {"help", "-h", "--help"}:
        _print_usage()
        return

    subcommand = ctx.args[0]
    args = ctx.args[1:]

    if subcommand == "create":
        include_dirty = False
        session_id: str | None = None
        idx = 0
        while idx < len(args):
            arg = args[idx]
            if arg == "--include-dirty":
                include_dirty = True
            elif arg == "--session-id":
                idx += 1
                if idx >= len(args):
                    print("checkpoint: --session-id requires a value")
                    return
                session_id = args[idx]
            else:
                print(f"checkpoint: unknown argument {arg!r}")
                _print_usage()
                return
            idx += 1

        try:
            record, existing_sha = create_checkpoint(
                target,
                session_id=session_id,
                include_dirty=include_dirty,
            )
        except CheckpointError as exc:
            print(f"checkpoint: {exc}")
            return

        if record is None:
            head = existing_sha[:12] if existing_sha else "?"
            print(f"Already checkpointed at {head} — nothing to do.")
        else:
            print(
                f"Checkpoint created: {record.head_sha[:12]} "
                f"(session={record.session_id})"
            )
        return

    if subcommand == "list":
        if args:
            print(f"checkpoint: unknown argument {args[0]!r}")
            _print_usage()
            return

        decision = classify(target)
        if decision.repo_root is None:
            print(f"checkpoint: {decision.reason}")
            return

        records = list_checkpoints(decision.repo_root)
        if not records:
            print("No checkpoints yet.")
            return

        current_sha = decision.head_sha or ""
        print(f"{'#':>3}  {'Session':<14}  {'Timestamp':<20}  {'HEAD':<12}  Workspace")
        for i, record in enumerate(records, start=1):
            marker = " *" if record.head_sha == current_sha else ""
            ts = record.timestamp[:19].replace("T", " ")
            print(
                f"{i:>3}  {record.session_id:<14}  {ts:<20}  "
                f"{record.head_sha[:12]:<12}  {record.workspace}{marker}"
            )
        return

    if subcommand == "diff":
        if len(args) != 1:
            print("checkpoint: diff requires a checkpoint identifier")
            _print_usage()
            return
        try:
            output = diff_checkpoint(target, args[0])
        except CheckpointError as exc:
            print(f"checkpoint: {exc}")
            return
        if output:
            print(output, end="")
        else:
            print("No changes since checkpoint.")
        return

    if subcommand == "restore":
        include_dirty = False
        remaining: list[str] = []
        for arg in args:
            if arg == "--include-dirty":
                include_dirty = True
            else:
                remaining.append(arg)
        if len(remaining) != 1:
            print("checkpoint: restore requires a checkpoint identifier")
            _print_usage()
            return
        try:
            result = restore_checkpoint(
                target,
                remaining[0],
                include_dirty=include_dirty,
            )
        except CheckpointError as exc:
            print(f"checkpoint: {exc}")
            return
        print(result)
        return

    print(f"checkpoint: unknown subcommand {subcommand!r}")
    _print_usage()
