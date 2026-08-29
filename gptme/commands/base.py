"""
Core command registry, decorator, and base types.
"""

import io
import logging
import re
from collections.abc import Callable, Generator
from contextlib import redirect_stdout
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

# Optional owning tool for dynamically registered commands. Process-global:
# sibling sessions on gptme-server share the registry. Dispatch consults the
# session-local loaded-tool set so a disable in this session cannot run the
# command here without yanking it from every other session.
_command_owners: dict[str, str] = {}


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
    owner_tool: str | None = None,
) -> None:
    """Register a command handler dynamically (for tools).

    Args:
        name: Command name (without leading /)
        handler: Function that takes CommandContext and yields Messages
        aliases: Optional list of command aliases
        completer: Optional function for argument completion.
                   Takes (partial_arg, previous_args) and returns list of (completion, description) tuples.
        owner_tool: Tool that owns this command. When set, dispatch and listing
                    require that tool to be loaded in the current session.
    """
    _command_registry[name] = handler
    names = [name, *(aliases or [])]
    if aliases:
        for alias in aliases:
            _command_registry[alias] = handler

    # Register completer if provided
    if completer:
        _command_completers[name] = completer
        if aliases:
            for alias in aliases:
                _command_completers[alias] = completer

    if owner_tool is not None:
        for cmd_name in names:
            _command_owners[cmd_name] = owner_tool
    else:
        # Re-registering without an owner must drop a stale mapping so the
        # command is unowned (always enabled), matching the docstring.
        for cmd_name in names:
            _command_owners.pop(cmd_name, None)

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
    _command_owners.pop(name, None)


def _command_enabled_in_session(name: str) -> bool:
    """True if this command may run in the current session.

    Unowned commands (built-ins, plugins) are always enabled. Tool-owned
    commands require their owner to be in the session-local loaded set.
    """
    owner = _command_owners.get(name)
    if owner is None:
        return True
    from ..tools import has_tool  # fmt: skip

    return has_tool(owner)


def get_registered_commands() -> list[str]:
    """Get list of all registered command names."""
    return list(_command_registry.keys())


def get_command_completer(name: str) -> CommandCompleter | None:
    """Get the completer function for a command.

    Args:
        name: Command name (without leading /)

    Returns:
        Completer function or None if no completer registered, or if the
        command's owning tool is not loaded in this session.
    """
    if not _command_enabled_in_session(name):
        return None
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


def _emit_json_command_output(content: str) -> None:
    """Emit captured command output as a JSON-mode assistant message."""
    from ..message import Message, print_msg  # fmt: skip

    content = content.rstrip("\n")
    if content:
        print_msg(Message("assistant", content))


def _collect_command_outputs(*captured_outputs: str) -> list[str]:
    """Collect distinct captured outputs while preserving order."""
    outputs: list[str] = []
    seen: set[str] = set()
    for output in captured_outputs:
        output = output.rstrip("\n")
        if output and output not in seen:
            outputs.append(output)
            seen.add(output)
    return outputs


def _yield_json_command_output(
    handler: CommandHandler,
    ctx: CommandContext,
) -> Generator["Message", None, None]:
    """Capture command stdout per generator step without hijacking yielded messages."""
    from ..util import console  # fmt: skip

    command_iter = iter(handler(ctx))
    while True:
        stdout_buffer = io.StringIO()
        next_msg = None
        completed = False
        error = None
        with console.capture() as console_capture, redirect_stdout(stdout_buffer):
            try:
                next_msg = next(command_iter)
            except StopIteration:
                completed = True
            except Exception as exc:  # pragma: no cover - passthrough
                error = exc

        outputs = _collect_command_outputs(
            stdout_buffer.getvalue(),
            console_capture.get(),
        )
        if outputs:
            _emit_json_command_output("\n".join(outputs))
        if error is not None:
            raise error
        if completed:
            return
        assert next_msg is not None
        yield next_msg


def handle_cmd(
    cmd: str,
    manager: "LogManager",
) -> Generator["Message", None, None]:
    """Handles a command."""
    from ..message import is_output_json  # fmt: skip

    cmd = cmd.lstrip("/")
    logger.debug(f"Executing command: {cmd}")
    name, *args = [s for s in re.split(r"[\n\s]", cmd) if s]
    full_args = cmd.split(" ", 1)[1] if " " in cmd else ""

    # Check if command is registered
    if name in _command_registry:
        if not _command_enabled_in_session(name):
            owner = _command_owners[name]
            manager.undo(1, quiet=True)
            msg = (
                f"Command /{name} is unavailable because tool '{owner}' "
                "is not enabled in this session."
            )
            if is_output_json():
                _emit_json_command_output(msg)
            else:
                print(msg)
            return
        handler = _command_registry[name]
        ctx = CommandContext(args=args, full_args=full_args, manager=manager)
        if is_output_json():
            yield from _yield_json_command_output(handler, ctx)
            return
        yield from handler(ctx)
        return

    # Fallback to tool execution
    from ..tools import ToolUse  # fmt: skip

    tooluse = ToolUse(name, [], full_args)
    if tooluse.is_runnable:
        yield from tooluse.execute(log=manager.log, workspace=manager.workspace)
    else:
        manager.undo(1, quiet=True)
        if is_output_json():
            _emit_json_command_output(
                "Unknown command. Use /help to see available commands."
            )
        else:
            print("Unknown command. Use /help to see available commands.")


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
    seen_handlers: set[int] = set()  # Track handler object IDs to skip aliases

    for name in _command_registry:
        if not _command_enabled_in_session(name):
            continue
        handler = _command_registry[name]
        handler_id = id(handler)
        if handler_id in seen_handlers:
            continue
        seen_handlers.add(handler_id)

        if name in desc_lookup:
            commands.append((name, desc_lookup[name]))
        else:
            # Fall back to handler docstring
            doc = getattr(handler, "__doc__", None)
            if not doc:
                wrapped = getattr(handler, "__wrapped__", None)
                if wrapped:
                    doc = getattr(wrapped, "__doc__", None)
            desc = doc.strip().split("\n")[0] if doc else f"/{name} command"
            commands.append((name, desc))

    return sorted(commands, key=lambda x: x[0])


def get_user_commands() -> list[str]:
    """Returns user commands enabled in this session.

    Includes built-ins and tool-registered commands whose owning tool is
    loaded. Process-global registrations of disabled tools are omitted.
    """
    return [f"/{cmd}" for cmd in _command_registry if _command_enabled_in_session(cmd)]
