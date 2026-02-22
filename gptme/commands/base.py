"""
Core command registry, decorator, and base types.
"""

import logging
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..logmanager import LogManager
    from ..message import Message

logger = logging.getLogger(__name__)


@dataclass
class CommandContext:
    """Context object containing all command handler parameters."""

    args: list[str]
    full_args: str
    manager: "LogManager"


# Original handler type (before decoration)
OriginalCommandHandler = (
    Callable[[CommandContext], Generator["Message", None, None]]
    | Callable[[CommandContext], None]
)

# Wrapped handler type (after decoration - always returns generator)
CommandHandler = Callable[[CommandContext], Generator["Message", None, None]]

# Completer function type: (partial_arg, previous_args) -> list of (completion, description)
CommandCompleter = Callable[[str, list[str]], list[tuple[str, str]]]

# Command registry
_command_registry: dict[str, CommandHandler] = {}

# Completer registry - maps command names to their completer functions
_command_completers: dict[str, CommandCompleter] = {}


def command(
    name: str,
    aliases: list[str] | None = None,
    completer: CommandCompleter | None = None,
    auto_undo: bool = True,
):
    """Decorator to register command handlers.

    Args:
        name: Command name (without leading /)
        aliases: Optional list of command aliases
        completer: Optional function for argument completion.
                   Takes (partial_arg, previous_args) and returns list of (completion, description) tuples.
        auto_undo: If True (default), automatically undo the command message before execution.
                   Set to False for commands that should be visible to the assistant
                   or that handle undo themselves.
    """

    def decorator(func: OriginalCommandHandler) -> OriginalCommandHandler:
        def wrapper(ctx: CommandContext) -> Generator:
            # Auto-undo the command message so it doesn't appear in the conversation
            if auto_undo:
                ctx.manager.undo(1, quiet=True)
                ctx.manager.write()

            result = func(ctx)
            if result is not None:
                # It's a generator, yield from it
                yield from result
            # If it's not a generator, we just don't yield anything

        _command_registry[name] = wrapper
        if aliases:
            for alias in aliases:
                _command_registry[alias] = wrapper

        # Register completer if provided
        if completer:
            _command_completers[name] = completer
            if aliases:
                for alias in aliases:
                    _command_completers[alias] = completer

        return func

    return decorator


def register_command(
    name: str,
    handler: CommandHandler,
    aliases: list[str] | None = None,
    completer: CommandCompleter | None = None,
) -> None:
    """Register a command handler dynamically (for tools).

    Args:
        name: Command name (without leading /)
        handler: Function that takes CommandContext and yields Messages
        aliases: Optional list of command aliases
        completer: Optional function for argument completion.
                   Takes (partial_arg, previous_args) and returns list of (completion, description) tuples.
    """
    _command_registry[name] = handler
    if aliases:
        for alias in aliases:
            _command_registry[alias] = handler

    # Register completer if provided
    if completer:
        _command_completers[name] = completer
        if aliases:
            for alias in aliases:
                _command_completers[alias] = completer

    logger.debug(
        f"Registered command: {name}" + (f" (aliases: {aliases})" if aliases else "")
    )


def unregister_command(name: str) -> None:
    """Unregister a command handler.

    Args:
        name: Command name to unregister
    """
    if name in _command_registry:
        del _command_registry[name]
        logger.debug(f"Unregistered command: {name}")
    if name in _command_completers:
        del _command_completers[name]


def get_registered_commands() -> list[str]:
    """Get list of all registered command names."""
    return list(_command_registry.keys())


def get_command_completer(name: str) -> CommandCompleter | None:
    """Get the completer function for a command.

    Args:
        name: Command name (without leading /)

    Returns:
        Completer function or None if no completer registered
    """
    return _command_completers.get(name)


def execute_cmd(msg: "Message", log: "LogManager") -> bool:
    """Executes any user-command, returns True if command was executed."""
    from ..util.content import is_message_command  # fmt: skip

    assert msg.role == "user"

    # if message starts with / treat as command
    # absolute paths dont trigger false positives by checking for single /
    if is_message_command(msg.content):
        for resp in handle_cmd(msg.content, log):
            log.append(resp)
        return True
    return False


def handle_cmd(
    cmd: str,
    manager: "LogManager",
) -> Generator["Message", None, None]:
    """Handles a command."""
    cmd = cmd.lstrip("/")
    logger.debug(f"Executing command: {cmd}")
    name, *args = re.split(r"[\n\s]", cmd)
    full_args = cmd.split(" ", 1)[1] if " " in cmd else ""

    # Check if command is registered
    if name in _command_registry:
        ctx = CommandContext(args=args, full_args=full_args, manager=manager)
        yield from _command_registry[name](ctx)
        return

    # Fallback to tool execution
    from ..tools import ToolUse  # fmt: skip

    tooluse = ToolUse(name, [], full_args)
    if tooluse.is_runnable:
        yield from tooluse.execute(log=manager.log, workspace=manager.workspace)
    else:
        manager.undo(1, quiet=True)
        print("Unknown command")


def get_commands_with_descriptions() -> list[tuple[str, str]]:
    """Get all registered commands with their descriptions.

    Returns a sorted list of (name, description) tuples for all registered commands.
    Uses action_descriptions for built-in commands, falls back to handler
    docstrings for dynamically registered commands.
    """
    from .meta import action_descriptions

    # Build a plain str->str lookup to avoid Literal key type constraints
    desc_lookup: dict[str, str] = {str(k): v for k, v in action_descriptions.items()}

    commands: list[tuple[str, str]] = []
    seen: set[str] = set()

    for name in _command_registry:
        if name in seen:
            continue
        seen.add(name)

        if name in desc_lookup:
            commands.append((name, desc_lookup[name]))
        else:
            # Fall back to handler docstring
            handler = _command_registry[name]
            doc = getattr(handler, "__doc__", None)
            if not doc:
                wrapped = getattr(handler, "__wrapped__", None)
                if wrapped:
                    doc = getattr(wrapped, "__doc__", None)
            desc = doc.strip().split("\n")[0] if doc else f"/{name} command"
            commands.append((name, desc))

    return sorted(commands, key=lambda x: x[0])


def get_user_commands() -> list[str]:
    """Returns a list of all user commands, including tool-registered commands"""
    # Get all registered commands (includes built-in + tool-registered)
    return [f"/{cmd}" for cmd in _command_registry]
