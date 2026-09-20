"""Durable conversation checkpoint slash command."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..checkpoint_session import (
    ConversationCheckpointError,
    list_conversation_checkpoints,
    save_conversation_checkpoint,
)
from .base import CommandContext, command

if TYPE_CHECKING:
    from ..logmanager import LogManager


def _print_usage() -> None:
    print("Usage: /session-checkpoint <save LABEL|list>")
    print()
    print("Commands:")
    print("  save LABEL   Save the current conversation state.")
    print("  list         List durable checkpoints for this conversation.")
    print()
    print("Resume from another process with:")
    print("  gptme checkpoint-session resume LABEL --session CONVERSATION")


def _save(manager: LogManager, label: str) -> None:
    checkpoint, path = save_conversation_checkpoint(
        manager.logdir, label, messages=manager.log.messages
    )
    print(
        f"Saved conversation checkpoint {checkpoint.label!r} "
        f"({checkpoint.message_count} messages): {path}"
    )


@command("session-checkpoint")
def cmd_session_checkpoint(ctx: CommandContext) -> None:
    """Save or list durable checkpoints for the current conversation."""
    if not ctx.args or ctx.args[0] in {"help", "-h", "--help"}:
        _print_usage()
        return

    subcommand = ctx.args[0]
    try:
        if subcommand == "save":
            if len(ctx.args) != 2:
                print("session-checkpoint: save requires one LABEL")
                _print_usage()
                return
            _save(ctx.manager, ctx.args[1])
            return

        if subcommand == "list":
            if len(ctx.args) != 1:
                print("session-checkpoint: list takes no arguments")
                _print_usage()
                return
            checkpoints = list_conversation_checkpoints(ctx.manager.logdir)
            if not checkpoints:
                print("No durable conversation checkpoints yet.")
                return
            for checkpoint in checkpoints:
                print(
                    f"{checkpoint.label:<24} {checkpoint.timestamp}  "
                    f"{checkpoint.message_count} messages"
                )
            return
    except (ConversationCheckpointError, OSError) as exc:
        print(f"session-checkpoint: {exc}")
        return

    print(f"session-checkpoint: unknown command {subcommand!r}")
    _print_usage()
