"""
Session management commands: log, undo, edit, rename, fork, delete, exit, restart, clear.
"""

import sys
from collections.abc import Generator
from time import sleep
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..logmanager import LogManager
    from ..message import Message

from .base import CommandContext, command


def _complete_log(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete log command flags."""
    completions: list[tuple[str, str]] = []
    if partial.startswith("-") or not partial:
        if "--hidden".startswith(partial):
            completions.append(("--hidden", "Show hidden system messages"))
    return completions


@command("log", completer=_complete_log)
def cmd_log(ctx: CommandContext) -> None:
    """Show the conversation log."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.log.print(show_hidden="--hidden" in ctx.args)


def _complete_rename(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete rename with suggestions."""
    completions: list[tuple[str, str]] = []
    if "auto".startswith(partial):
        completions.append(("auto", "Auto-generate name from conversation"))
    return completions


@command("rename", completer=_complete_rename)
def cmd_rename(ctx: CommandContext) -> None:
    """Rename the conversation."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    # rename the conversation
    print("Renaming conversation")
    if ctx.args:
        new_name = ctx.args[0]
    else:
        print("(enter empty name to auto-generate)")
        new_name = input("New name: ").strip()
    _rename(ctx.manager, new_name, ctx.confirm)


def _complete_fork(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete fork with conversation name suggestions."""
    import time

    completions: list[tuple[str, str]] = []
    # Suggest a timestamped fork name
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    suggestion = f"fork-{timestamp}"
    if suggestion.startswith(partial) or not partial:
        completions.append((suggestion, "Timestamped fork name"))
    return completions


@command("fork", completer=_complete_fork)
def cmd_fork(ctx: CommandContext) -> None:
    """Fork the conversation."""
    ctx.manager.undo(1, quiet=True)
    new_name = ctx.args[0] if ctx.args else input("New name: ")
    ctx.manager.fork(new_name)
    print(f"✅ Forked conversation to: {ctx.manager.logdir}")


def _complete_delete(partial: str, prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete conversation IDs for deletion."""
    from ..logmanager import list_conversations  # fmt: skip

    completions: list[tuple[str, str]] = []

    # Check for flags
    if partial.startswith("-"):
        if "--force".startswith(partial):
            completions.append(("--force", "Delete without confirmation"))
        if "-f".startswith(partial):
            completions.append(("-f", "Delete without confirmation"))
        return completions

    # Get recent conversations
    conversations = list_conversations(limit=20)
    for conv in conversations:
        if conv.id.startswith(partial) or conv.name.lower().startswith(partial.lower()):
            completions.append((conv.id, conv.name or ""))

    return completions


@command("delete", aliases=["rm"], completer=_complete_delete)
def cmd_delete(ctx: CommandContext) -> None:
    """Delete a conversation by ID.

    Usage:
        /delete           - List recent conversations with their IDs
        /delete <id>      - Delete the conversation with the given ID
        /delete --force <id> - Delete without confirmation
    """
    from ..logmanager import delete_conversation, list_conversations  # fmt: skip

    ctx.manager.undo(1, quiet=True)

    # Check for --force flag
    force = "--force" in ctx.args or "-f" in ctx.args
    args = [a for a in ctx.args if a not in ("--force", "-f")]

    if not args:
        # List conversations to help user find the ID
        conversations = list_conversations(limit=10)
        if not conversations:
            print("No conversations found.")
            return

        print("Recent conversations (use /delete <id> to delete):\n")
        for i, conv in enumerate(conversations, 1):
            # Mark current conversation
            is_current = ctx.manager.logdir.name == conv.id
            marker = " (current)" if is_current else ""
            print(f"  {i}. {conv.name} [id: {conv.id}]{marker}")
        print("\nNote: Cannot delete the current conversation.")
        return

    conv_id = args[0]

    # Prevent deleting current conversation
    if ctx.manager.logdir.name == conv_id:
        print("❌ Cannot delete the current conversation.")
        print("   Start a new conversation first, then delete this one.")
        return

    # Confirm deletion unless --force
    if not force:
        if not ctx.confirm(f"Delete conversation '{conv_id}'? This cannot be undone."):
            print("Cancelled.")
            return

    # Attempt deletion
    if delete_conversation(conv_id):
        print(f"✅ Deleted conversation: {conv_id}")
    else:
        print(f"❌ Conversation not found: {conv_id}")


@command("edit")
def cmd_edit(ctx: CommandContext) -> Generator["Message", None, None]:
    """Edit previous messages."""
    # first undo the '/edit' command itself
    ctx.manager.undo(1, quiet=True)
    yield from _edit(ctx.manager)


@command("undo")
def cmd_undo(ctx: CommandContext) -> None:
    """Undo the last action(s)."""
    # undo the '/undo' command itself
    ctx.manager.undo(1, quiet=True)
    # if int, undo n messages
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
    ctx.manager.undo(n)


@command("clear", aliases=["cls"])
def cmd_clear(ctx: CommandContext) -> None:
    """Clear the terminal screen."""
    ctx.manager.undo(1, quiet=True)
    # ANSI escape code to clear screen and move cursor to home position
    print("\033[2J\033[H", end="")


@command("exit")
def cmd_exit(ctx: CommandContext) -> None:
    """Exit the program."""
    from ..hooks import HookType, trigger_hook

    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()

    # Trigger session end hooks before exiting
    logdir = ctx.manager.logdir
    for msg in trigger_hook(HookType.SESSION_END, logdir=logdir, manager=ctx.manager):
        ctx.manager.append(msg)
    ctx.manager.write()

    sys.exit(0)


@command("restart")
def cmd_restart(ctx: CommandContext) -> None:
    """Restart the gptme process.

    Useful for:
    - Applying configuration changes that require a restart
    - Reloading tools after code modifications
    - Recovering from state issues
    """
    from ..tools.restart import _do_restart

    ctx.manager.undo(1, quiet=True)

    if not ctx.confirm("Restart gptme? This will exit and restart the process."):
        print("Restart cancelled.")
        return

    # Ensure everything is synced to disk
    ctx.manager.write(sync=True)

    conversation_name = ctx.manager.logdir.name
    print(f"Restarting gptme with conversation: {conversation_name}")

    # Perform the restart
    _do_restart(conversation_name)


def _edit(
    manager: "LogManager",
) -> Generator["Message", None, None]:  # pragma: no cover
    """Edit messages in editor."""
    from ..constants import INTERRUPT_CONTENT  # fmt: skip
    from ..message import Message, msgs_to_toml, toml_to_msgs  # fmt: skip
    from ..util.useredit import edit_text_with_editor  # fmt: skip

    # generate editable toml of all messages
    t = msgs_to_toml(reversed(manager.log))  # type: ignore
    res = None
    while not res:
        t = edit_text_with_editor(t, "toml")
        try:
            res = toml_to_msgs(t)
        except Exception as e:
            print(f"\nFailed to parse TOML: {e}")
            try:
                sleep(1)
            except KeyboardInterrupt:
                yield Message("system", INTERRUPT_CONTENT)
                return
    manager.edit(list(reversed(res)))
    print("Applied edited messages, write /log to see the result")


def _rename(manager: "LogManager", new_name: str, confirm) -> None:
    """Rename a conversation."""
    from ..config import ChatConfig  # fmt: skip
    from ..logmanager import prepare_messages  # fmt: skip
    from ..util.auto_naming import generate_llm_name  # fmt: skip

    if new_name in ["", "auto"]:
        msgs = prepare_messages(manager.log.messages)[1:]  # skip system message
        new_name = generate_llm_name(msgs)
        assert " " not in new_name, f"Invalid name: {new_name}"
        print(f"Generated name: {new_name}")
        if not confirm("Confirm?"):
            print("Aborting")
            return

    # Load or create chat config and update the name
    chat_config = ChatConfig.from_logdir(manager.logdir)
    chat_config.name = new_name
    chat_config.save()

    print(f"Renamed conversation to: {new_name}")
