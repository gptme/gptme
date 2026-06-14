"""
Workspace snapshot commands for fine-grained rollback.

``/snapshot`` exposes the side-git auto-snapshot backend
(:mod:`gptme.workspace_snapshot`) as interactive slash commands.
Agents can list available snapshots and restore the workspace to any prior
state — no clean-git requirement, and uncommitted changes are preserved in the
snapshot ledger.

This is **different** from ``/checkpoint``:

- ``/checkpoint`` records a git-committed HEAD so you can ``git reset --hard``
  back to it.  Requires a clean working tree by default.
- ``/snapshot`` records *any* workspace state (committed or dirty) into a
  side-git shadow repo.  Auto-populated by the ``auto_snapshots`` hook.

For agents that tree-search (#495): take an auto-snapshot before each attempt,
then ``/snapshot restore <sha>`` to roll back a failed branch.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..workspace_snapshot import (
    Shadow,
    get_snapshot_n_msgs,
    init_shadow,
    list_snapshots,
    restore,
    snapshot,
)
from .base import CommandContext, command


def _print_usage() -> None:
    print("Usage: /snapshot <create|list|restore|diff> ...")
    print()
    print("Subcommands:")
    print("  create [label]       Record the current workspace state as a snapshot.")
    print("  list [--limit N]     List recent snapshots (newest first).")
    print("  restore <sha>        Restore workspace to a snapshot.")
    print("  diff <sha>           Show diff between current workspace and a snapshot.")


def _workspace_path(ctx: CommandContext) -> Path | None:
    workspace = getattr(ctx.manager, "workspace", None)
    if workspace is None:
        print("snapshot: no workspace configured for this session")
        return None
    return Path(workspace)


def _workspace_shadow(ctx: CommandContext) -> Shadow | None:
    workspace = _workspace_path(ctx)
    if workspace is None:
        return None
    shadow = Shadow.for_workspace(workspace)
    if not shadow.initialized():
        print(
            "snapshot: no snapshot history for this workspace "
            "(enable auto-snapshots via GPTME_AUTO_SNAPSHOTS=1 or the auto_snapshots plugin)"
        )
        return None
    return shadow


@command("snapshot")
def cmd_snapshot(ctx: CommandContext) -> None:
    """List, diff, or restore workspace auto-snapshots.

    Snapshots are created automatically by the auto_snapshots hook before and
    after each mutating tool call.  Use this command to inspect the history,
    restore a prior state, or explicitly record a named snapshot.

    Usage:
      /snapshot create [label]     Record current workspace state
      /snapshot list [--limit N]   Show recent snapshots
      /snapshot restore <sha>      Roll back to a snapshot
      /snapshot diff <sha>         Show diff from current to snapshot
    """
    if not ctx.args or ctx.args[0] in {"help", "-h", "--help"}:
        _print_usage()
        return

    subcommand = ctx.args[0]
    args = ctx.args[1:]

    if subcommand == "create":
        label = args[0] if args else "manual"
        workspace = _workspace_path(ctx)
        if workspace is None:
            return
        shadow = Shadow.for_workspace(workspace)
        if not shadow.initialized():
            shadow = init_shadow(workspace)
        n_msgs = len(ctx.manager.log) if ctx.manager is not None else None
        sha = snapshot(shadow, label=label, n_msgs=n_msgs)
        if sha is not None:
            print(f"Snapshot recorded: {sha}  ({label})")
        else:
            print("snapshot: failed to record snapshot")
        return

    if subcommand == "list":
        limit = 20
        idx = 0
        while idx < len(args):
            arg = args[idx]
            if arg in ("--limit", "-n"):
                idx += 1
                if idx >= len(args):
                    print("snapshot: --limit requires a value")
                    return
                try:
                    limit = int(args[idx])
                except ValueError:
                    print(f"snapshot: --limit must be an integer, got {args[idx]!r}")
                    return
            else:
                print(f"snapshot: unknown argument {arg!r}")
                _print_usage()
                return
            idx += 1

        workspace_shadow = _workspace_shadow(ctx)
        if workspace_shadow is None:
            return

        entries = list_snapshots(workspace_shadow, limit=limit)
        if not entries:
            print("No snapshots yet.")
            return
        print(f"{'SHA':<9}  Label")
        print("-" * 40)
        for sha, label in entries:
            print(f"{sha:<9}  {label}")
        return

    if subcommand == "restore":
        if len(args) != 1:
            print("snapshot: restore requires exactly one SHA argument")
            _print_usage()
            return

        workspace_shadow = _workspace_shadow(ctx)
        if workspace_shadow is None:
            return

        sha = args[0]
        ok = restore(workspace_shadow, sha)
        if ok:
            print(f"Restored workspace to snapshot {sha}.")
        else:
            print(
                f"snapshot: restore failed for {sha!r} "
                "(check that the SHA is from /snapshot list)"
            )
        return

    if subcommand == "diff":
        if len(args) != 1:
            print("snapshot: diff requires exactly one SHA argument")
            _print_usage()
            return

        workspace_shadow = _workspace_shadow(ctx)
        if workspace_shadow is None:
            return

        sha = args[0]

        # Conversation summary: show messages added since the snapshot was taken.
        snap_n_msgs = get_snapshot_n_msgs(workspace_shadow, sha)
        if snap_n_msgs is not None and ctx.manager is not None:
            current_msgs = list(ctx.manager.log)
            delta = len(current_msgs) - snap_n_msgs
            if delta > 0:
                role_counts = Counter(m.role for m in current_msgs[snap_n_msgs:])
                role_str = ", ".join(
                    f"{count} {role}" for role, count in sorted(role_counts.items())
                )
                print(
                    f"+{delta} message{'s' if delta != 1 else ''} since snapshot {sha} ({role_str})"
                )
            elif delta == 0:
                print(f"No new messages since snapshot {sha}.")
            else:
                print(
                    f"Conversation is shorter than at snapshot time "
                    f"({len(current_msgs)} vs {snap_n_msgs} messages)."
                )
            print()

        # Workspace diff.
        result = workspace_shadow.run("diff", sha, check=False)
        if result.returncode != 0:
            print(f"snapshot: diff failed: {result.stderr.strip() or 'unknown error'}")
            return
        output = result.stdout
        if output:
            print(output, end="")
        else:
            print(f"No changes between current workspace and snapshot {sha}.")
        return

    print(f"snapshot: unknown subcommand {subcommand!r}")
    _print_usage()
