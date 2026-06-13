"""
Conversation backtracking: /backtrack mark|list|<target> [--reason <text>]

Lets the user save named checkpoints in the conversation log and rewind to them
after a failed tool run or bad exchange.  The rollback injects a short system
message that tells the model *why* we rewound so retries are more informed.

This implements Phase 1 of gptme/gptme#523 (manual conversation backtracking).
Filesystem state is NOT rolled back — use /checkpoint for that.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..message import Message

from ..logmanager.conv_checkpoints import (
    list_conv_checkpoints,
    resolve_conv_checkpoint,
    save_conv_checkpoint,
)
from .base import CommandContext, command

# Separator used in /backtrack help output
_SEP = "  "


def _print_usage() -> None:
    print("Usage: /backtrack <mark [label] | list | <target> [--reason <text>]>")
    print()
    print("Subcommands:")
    print(f"  mark [label]{_SEP}Save the current message index as a named checkpoint.")
    print(f"  list{_SEP * 4}List saved checkpoints for this conversation.")
    print(f"  <label|N>{_SEP * 3}Rewind to a checkpoint label or message index N.")
    print()
    print("Options for rewind:")
    print(f"  --reason <text>{_SEP}Inject a corrective summary explaining the rewind.")
    print(
        f"  <label> <text> {_SEP}Shorthand: extra words after the label become the reason."
    )


def _auto_label(records_count: int) -> str:
    return f"cp{records_count + 1}"


def _complete_backtrack(partial: str, _prev: list[str]) -> list[tuple[str, str]]:
    # Checkpoint labels are not included here because the completer runs without
    # access to the LogManager (and thus the sidecar file). Use /backtrack list
    # to see available labels; a closure-based completer could surface them in future.
    completions: list[tuple[str, str]] = [
        ("mark", "Save current position as a checkpoint"),
        ("list", "List saved checkpoints"),
    ]
    # filter by partial
    return [(c, d) for c, d in completions if c.startswith(partial)]


@command("backtrack", completer=_complete_backtrack)
def cmd_backtrack(ctx: CommandContext) -> Generator[Message, None, None]:
    """Rewind the conversation to a checkpoint or message index.

    Usage:
      /backtrack mark [label]          Save a named checkpoint
      /backtrack list                  List checkpoints
      /backtrack <label|N> [--reason]  Rewind to checkpoint/index
    """
    from ..message import Message  # fmt: skip

    args = list(ctx.args)
    if not args or args[0] in {"help", "-h", "--help"}:
        _print_usage()
        return

    subcommand = args[0]

    # ── mark ─────────────────────────────────────────────────────────────────
    if subcommand == "mark":
        label = args[1] if len(args) > 1 else None
        index = len(ctx.manager.log.messages)
        existing = list_conv_checkpoints(ctx.manager.logdir)
        if label is None:
            label = _auto_label(len(existing))
        # Warn if a checkpoint with this label already exists
        existing_labels = [r for r in existing if r.label == label]
        if existing_labels:
            prev = existing_labels[-1]
            print(
                f"Warning: checkpoint '{label}' already exists at index {prev.index}; "
                f"creating a new one at index {index}."
            )
        record = save_conv_checkpoint(ctx.manager.logdir, index, label)
        print(f"Checkpoint '{record.label}' saved at message index {record.index}.")
        return

    # ── list ─────────────────────────────────────────────────────────────────
    if subcommand == "list":
        records = list_conv_checkpoints(ctx.manager.logdir)
        if not records:
            print("No checkpoints yet.  Use /backtrack mark [label] to save one.")
            return
        print(f"{'#':>3}  {'Label':<16}  {'Index':>5}  Timestamp")
        for i, r in enumerate(records, start=1):
            ts = r.timestamp[:19].replace("T", " ")
            print(f"{i:>3}  {r.label:<16}  {r.index:>5}  {ts}")
        return

    # ── rewind ───────────────────────────────────────────────────────────────
    target = subcommand  # label or bare integer

    # Parse --reason
    reason: str | None = None
    remaining_args = args[1:]
    reason_idx = next(
        (i for i, a in enumerate(remaining_args) if a == "--reason"),
        None,
    )
    if reason_idx is not None:
        if reason_idx + 1 < len(remaining_args):
            reason = " ".join(remaining_args[reason_idx + 1 :])
        else:
            print("backtrack: --reason requires a value")
            return
    elif remaining_args and not remaining_args[0].startswith("--"):
        # Allow: /backtrack 5 optional reason without --reason flag
        reason = " ".join(remaining_args)

    # Resolve target → checkpoint record
    try:
        record = resolve_conv_checkpoint(ctx.manager.logdir, target)
    except KeyError as exc:
        print(f"backtrack: {exc}")
        return

    current_len = len(ctx.manager.log.messages)
    rewind_to = record.index

    if rewind_to < 0:
        print(f"backtrack: invalid index {rewind_to}")
        return
    if rewind_to > current_len:
        print(
            f"backtrack: checkpoint index {rewind_to} is beyond current "
            f"conversation length ({current_len} messages)"
        )
        return
    if rewind_to == current_len:
        print(f"backtrack: already at message index {current_len} — nothing to rewind.")
        return

    removed = current_len - rewind_to

    # Truncate the log (edit() saves a backup branch automatically)
    new_messages = list(ctx.manager.log.messages[:rewind_to])
    ctx.manager.edit(new_messages)

    # Build the corrective summary message
    if reason:
        # strip surrounding quotes that some shells add
        reason = re.sub(r'^["\']|["\']$', "", reason).strip()

    summary_parts = [
        f"[Backtracked {removed} message(s) to index {rewind_to}",
        f" (checkpoint: '{record.label}')" if not record.label.startswith("@") else "",
        "]",
    ]
    if reason:
        summary_parts.append(f"\n{reason}")
    summary = "".join(summary_parts)

    summary_msg = Message("system", summary, hide=True)
    yield summary_msg
