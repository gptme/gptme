import logging
import re
import shlex
import sys
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from typing import Literal

from . import llm
from .config import ChatConfig
from .constants import INTERRUPT_CONTENT
from .llm.models import get_default_model, list_models, set_default_model
from .logmanager import Log, LogManager, prepare_messages
from .message import (
    Message,
    len_tokens,
    msgs_to_toml,
    print_msg,
    toml_to_msgs,
)
from .setup import setup
from .tools import (
    ConfirmFunc,
    ToolUse,
    execute_msg,
    get_tool,
    get_tool_format,
    get_tools,
)
from .util.auto_compact import auto_compact_log, should_auto_compact
from .util.auto_naming import generate_llm_name
from .util.cost import log_costs
from .util.export import export_chat_to_html
from .util.useredit import edit_text_with_editor

logger = logging.getLogger(__name__)

Actions = Literal[
    "log",
    "undo",
    "edit",
    "rename",
    "fork",
    "tools",
    "model",
    "replay",
    "impersonate",
    "summarize",
    "compact",
    "tokens",
    "export",
    "commit",
    "setup",
    "help",
    "exit",
]

action_descriptions: dict[Actions, str] = {
    "undo": "Undo the last action",
    "log": "Show the conversation log",
    "tools": "Show available tools",
    "model": "List or switch models",
    "edit": "Edit the conversation in your editor",
    "rename": "Rename the conversation",
    "fork": "Copy the conversation using a new name",
    "summarize": "Summarize the conversation",
    "replay": "Replay tool operations",
    "compact": "Compact conversation using auto-compacting or LLM resume generation",
    "impersonate": "Impersonate the assistant",
    "tokens": "Show the number of tokens used",
    "export": "Export conversation as HTML",
    "commit": "Ask assistant to git commit",
    "setup": "Setup gptme with completions and configuration",
    "help": "Show this help message",
    "exit": "Exit the program",
}
COMMANDS = list(action_descriptions.keys())


@dataclass
class CommandContext:
    """Context object containing all command handler parameters."""

    args: list[str]
    full_args: str
    manager: LogManager
    confirm: ConfirmFunc


# Original handler type (before decoration)
OriginalCommandHandler = (
    Callable[[CommandContext], Generator[Message, None, None]]
    | Callable[[CommandContext], None]
)

# Wrapped handler type (after decoration - always returns generator)
CommandHandler = Callable[[CommandContext], Generator[Message, None, None]]

# Command registry
_command_registry: dict[str, CommandHandler] = {}


def command(name: str, aliases: list[str] | None = None):
    """Decorator to register command handlers."""

    def decorator(func: OriginalCommandHandler) -> OriginalCommandHandler:
        def wrapper(ctx: CommandContext) -> Generator[Message, None, None]:
            result = func(ctx)
            if result is not None:
                # It's a generator, yield from it
                yield from result
            # If it's not a generator, we just don't yield anything

        _command_registry[name] = wrapper
        if aliases:
            for alias in aliases:
                _command_registry[alias] = wrapper
        return func

    return decorator


def register_command(
    name: str, handler: CommandHandler, aliases: list[str] | None = None
) -> None:
    """Register a command handler dynamically (for tools).

    Args:
        name: Command name (without leading /)
        handler: Function that takes CommandContext and yields Messages
        aliases: Optional list of command aliases
    """
    _command_registry[name] = handler
    if aliases:
        for alias in aliases:
            _command_registry[alias] = handler
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


def get_registered_commands() -> list[str]:
    """Get list of all registered command names."""
    return list(_command_registry.keys())


@command("log")
def cmd_log(ctx: CommandContext) -> None:
    """Show the conversation log."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.log.print(show_hidden="--hidden" in ctx.args)


@command("rename")
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
    rename(ctx.manager, new_name, ctx.confirm)


@command("fork")
def cmd_fork(ctx: CommandContext) -> None:
    """Fork the conversation."""
    ctx.manager.undo(1, quiet=True)
    new_name = ctx.args[0] if ctx.args else input("New name: ")
    ctx.manager.fork(new_name)
    print(f"✅ Forked conversation to: {ctx.manager.logdir}")


@command("summarize")
def cmd_summarize(ctx: CommandContext) -> None:
    """Summarize the conversation."""
    ctx.manager.undo(1, quiet=True)
    msgs = prepare_messages(ctx.manager.log.messages)
    msgs = [m for m in msgs if not m.hide]
    print_msg(llm.summarize(msgs))


@command("compact")
def cmd_compact(ctx: CommandContext) -> Generator[Message, None, None]:
    """Compact the conversation using auto-compacting or LLM-powered resume generation."""
    ctx.manager.undo(1, quiet=True)

    # Parse arguments
    method = ctx.args[0] if ctx.args else "auto"

    if method not in ["auto", "resume"]:
        yield Message(
            "system",
            "Invalid compact method. Use 'auto' for auto-compacting or 'resume' for LLM-powered resume generation.\n"
            "Usage: /compact [auto|resume]",
        )
        return

    msgs = ctx.manager.log.messages[:-1]  # Exclude the /compact command itself

    if method == "auto":
        yield from _compact_auto(ctx, msgs)
    elif method == "resume":
        yield from _compact_resume(ctx, msgs)


def _compact_auto(
    ctx: CommandContext, msgs: list[Message]
) -> Generator[Message, None, None]:
    """Auto-compact using the aggressive compacting algorithm."""
    if not should_auto_compact(msgs):
        yield Message(
            "system",
            "Auto-compacting not needed. Conversation doesn't contain massive tool results or isn't close to context limits.",
        )
        return

    # Apply auto-compacting
    compacted_msgs = list(auto_compact_log(msgs))

    # Calculate reduction stats
    original_count = len(msgs)
    compacted_count = len(compacted_msgs)
    m = get_default_model()
    original_tokens = len_tokens(msgs, m.model) if m else 0
    compacted_tokens = len_tokens(compacted_msgs, m.model) if m else 0

    # Replace the conversation history
    ctx.manager.log = Log(compacted_msgs)
    ctx.manager.write()

    yield Message(
        "system",
        f"✅ Auto-compacting completed:\n"
        f"• Messages: {original_count} → {compacted_count}\n"
        f"• Tokens: {original_tokens:,} → {compacted_tokens:,} "
        f"({((original_tokens - compacted_tokens) / original_tokens * 100):.1f}% reduction)",
    )


def _compact_resume(
    ctx: CommandContext, msgs: list[Message]
) -> Generator[Message, None, None]:
    """LLM-powered compact that creates RESUME.md, suggests files to include, and starts a new conversation with the context."""

    # Prepare messages for summarization
    prepared_msgs = prepare_messages(msgs)
    visible_msgs = [m for m in prepared_msgs if not m.hide]

    if len(visible_msgs) < 3:
        yield Message(
            "system", "Not enough conversation history to create a meaningful resume."
        )
        return

    # Generate conversation summary using LLM
    yield Message("system", "🔄 Generating conversation resume with LLM...")

    resume_prompt = """Please create a comprehensive resume of this conversation that includes:

1. **Conversation Summary**: Key topics, decisions made, and progress achieved
2. **Technical Context**: Important code changes, configurations, or technical details
3. **Current State**: What was accomplished and what remains to be done
4. **Context Files**: Suggest which files should be included in future context (with brief rationale)

Format the response as a structured document that could serve as a RESUME.md file."""

    # Create a temporary message for the LLM prompt
    resume_request = Message("user", resume_prompt)
    llm_msgs = visible_msgs + [resume_request]

    try:
        # Generate the resume using LLM
        m = get_default_model()
        assert m
        resume_response = llm.reply(llm_msgs, model=m.model, tools=[])
        resume_content = resume_response.content

        # Save RESUME.md file
        resume_path = Path("RESUME.md")
        with open(resume_path, "w") as f:
            f.write(resume_content)

        # Create a compact conversation with just the resume
        system_msg = Message(
            "system", f"Previous conversation resumed from {resume_path}:"
        )
        resume_msg = Message("assistant", resume_content)

        # Replace conversation history with resume
        # TODO: fork into a new conversation?
        ctx.manager.log = Log([system_msg, resume_msg])
        ctx.manager.write()

        yield Message(
            "system",
            f"✅ LLM-powered resume completed:\n"
            f"• Original conversation ({len(visible_msgs)} messages) compressed to resume\n"
            f"• Resume saved to: {resume_path.absolute()}\n"
            f"• Conversation history replaced with resume\n"
            f"• Review the RESUME.md file for suggested context files",
        )

    except Exception as e:
        yield Message("system", f"❌ Failed to generate resume: {e}")


@command("edit")
def cmd_edit(ctx: CommandContext) -> Generator[Message, None, None]:
    """Edit previous messages."""
    # first undo the '/edit' command itself
    ctx.manager.undo(1, quiet=True)
    yield from edit(ctx.manager)


@command("undo")
def cmd_undo(ctx: CommandContext) -> None:
    """Undo the last action(s)."""
    # undo the '/undo' command itself
    ctx.manager.undo(1, quiet=True)
    # if int, undo n messages
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 1
    ctx.manager.undo(n)


@command("exit")
def cmd_exit(ctx: CommandContext) -> None:
    """Exit the program."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    sys.exit(0)


@command("replay")
def cmd_replay(ctx: CommandContext) -> None:
    """Replay the conversation or specific tool operations."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()

    # Check if replaying a specific tool
    if ctx.args and ctx.args[0].lower() not in ["last", "all"]:
        tool_name = ctx.args[0]
        _replay_tool(ctx.manager.log, tool_name)
        return

    # Determine replay scope for messages
    if ctx.args:
        scope = ctx.args[0].lower()
        if scope not in ["last", "all"]:
            print(f"Invalid option '{scope}'. Use 'last', 'all', or a tool name.")
            return
    else:
        print("Replay options:")
        print("  last - Replay only the last assistant message")
        print("  all  - Replay all assistant messages")
        print("  <tool> - Replay all operations for a specific tool (e.g., todowrite)")
        scope = input("Choose (last/all/<tool>): ").strip().lower()
        if scope not in ["last", "all"]:
            # Try as tool name
            _replay_tool(ctx.manager.log, scope)
            return

    assistant_messages = [msg for msg in ctx.manager.log if msg.role == "assistant"]

    if not assistant_messages:
        print("No assistant messages found to replay.")
        return

    if scope == "last":
        # Find the last assistant message that contains tool uses
        last_with_tools = None
        for msg in reversed(assistant_messages):
            # Check if message has any tool uses by trying to execute it
            if any(ToolUse.iter_from_content(msg.content)):
                last_with_tools = msg
                break

        if not last_with_tools:
            print("No assistant messages with tool uses found to replay.")
            return

        messages_to_replay = [last_with_tools]
        print("Replaying last assistant message with tool uses...")
    else:  # scope == "all"
        messages_to_replay = assistant_messages
        print(f"Replaying all {len(assistant_messages)} assistant messages...")

    for msg in messages_to_replay:
        for reply_msg in execute_msg(msg, ctx.confirm):
            print_msg(reply_msg, oneline=False)


def _replay_tool(log, tool_name: str) -> None:
    """Replay all operations for a specific tool from the conversation log."""

    tool = get_tool(tool_name)
    if not tool:
        print(f"Error: Tool '{tool_name}' not found or not loaded.")
        return

    print(f"Replaying all '{tool_name}' operations...")
    count = 0

    for msg in log:
        for tooluse in ToolUse.iter_from_content(msg.content):
            if tooluse.tool == tool_name and tooluse.content:
                count += 1
                # Execute the tool operation
                lines = [
                    line.strip()
                    for line in tooluse.content.strip().split("\n")
                    if line.strip()
                ]

                for line in lines:
                    # Use the tool's execute function directly
                    # For tools like todowrite, this will update internal state
                    try:
                        parts = shlex.split(line)
                        if parts:
                            # Import the tool's helper function if it exists
                            # For todowrite, this would be _todowrite
                            helper_name = f"_{tool_name}"
                            tool_module = __import__(
                                f"gptme.tools.{tool_name}",
                                fromlist=[helper_name],
                            )
                            if hasattr(tool_module, helper_name):
                                helper_func = getattr(tool_module, helper_name)
                                result = helper_func(*parts)
                                if result:
                                    print(f"  {result}")
                    except Exception as e:
                        logger.warning(
                            f"Failed to replay {tool_name} operation '{line}': {e}"
                        )

    if count == 0:
        print(f"No '{tool_name}' operations found to replay.")
    else:
        print(f"✅ Replayed {count} '{tool_name}' operations")


@command("impersonate")
def cmd_impersonate(ctx: CommandContext) -> Generator[Message, None, None]:
    """Impersonate the assistant."""
    content = ctx.full_args if ctx.full_args else input("[impersonate] Assistant: ")
    msg = Message("assistant", content)
    yield msg
    yield from execute_msg(msg, confirm=lambda _: True)


@command("tokens")
def cmd_tokens(ctx: CommandContext) -> None:
    """Show token usage."""
    ctx.manager.undo(1, quiet=True)
    log_costs(ctx.manager.log.messages)


@command("tools")
def cmd_tools(ctx: CommandContext) -> None:
    """Show available tools."""
    ctx.manager.undo(1, quiet=True)
    print("Available tools:")
    for tool in get_tools():
        print(
            f"""
  # {tool.name}
    {tool.desc.rstrip(".")}
    tokens (example): {len_tokens(tool.get_examples(get_tool_format()), "gpt-4")}"""
        )


@command("model", aliases=["models"])
def cmd_model(ctx: CommandContext) -> None:
    """List or switch models."""
    ctx.manager.undo(1, quiet=True)
    if ctx.args:
        set_default_model(ctx.args[0])
        print(f"Set model to {ctx.args[0]}")
    else:
        model = get_default_model()
        assert model
        print(f"Current model: {model.full}")
        print(
            f"  price: input ${model.price_input}/Mtok, output ${model.price_output}/Mtok"
        )
        print(f"  context: {model.context}, max output: {model.max_output}")
        print(
            f"  (streaming: {model.supports_streaming}, vision: {model.supports_vision})"
        )

        print_available_models()


@command("export")
def cmd_export(ctx: CommandContext) -> None:
    """Export conversation as HTML."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    # Get output path from args or use default
    output_path = (
        Path(ctx.args[0])
        if ctx.args
        else Path(f"{ctx.manager.logfile.parent.name}.html")
    )
    # Export the chat
    export_chat_to_html(ctx.manager.name, ctx.manager.log, output_path)
    print(f"Exported conversation to {output_path}")


# Note: /commit command is now registered by the autocommit tool
# Note: /pre-commit command is registered by the precommit tool


@command("setup")
def cmd_setup(ctx: CommandContext) -> None:
    """Setup gptme with completions, configuration, and project setup."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    setup()


@command("help")
def cmd_help(ctx: CommandContext) -> None:
    """Show help message."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    help()


def execute_cmd(msg: Message, log: LogManager, confirm: ConfirmFunc) -> bool:
    """Executes any user-command, returns True if command was executed."""
    assert msg.role == "user"

    # if message starts with / treat as command
    # when command has been run,
    if msg.content[:1] in ["/"]:
        for resp in handle_cmd(msg.content, log, confirm):
            log.append(resp)
        return True
    return False


def handle_cmd(
    cmd: str,
    manager: LogManager,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Handles a command."""
    cmd = cmd.lstrip("/")
    logger.debug(f"Executing command: {cmd}")
    name, *args = re.split(r"[\n\s]", cmd)
    full_args = cmd.split(" ", 1)[1] if " " in cmd else ""

    # Check if command is registered
    if name in _command_registry:
        ctx = CommandContext(
            args=args, full_args=full_args, manager=manager, confirm=confirm
        )
        yield from _command_registry[name](ctx)
        return

    # Fallback to tool execution
    tooluse = ToolUse(name, [], full_args)
    if tooluse.is_runnable:
        yield from tooluse.execute(confirm)
    else:
        manager.undo(1, quiet=True)
        print("Unknown command")


def edit(manager: LogManager) -> Generator[Message, None, None]:  # pragma: no cover
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


def rename(manager: LogManager, new_name: str, confirm: ConfirmFunc) -> None:
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


def _gen_help(incl_langtags: bool = True) -> Generator[str, None, None]:
    yield "Available commands:"
    max_cmdlen = max(len(cmd) for cmd in COMMANDS)
    for cmd, desc in action_descriptions.items():
        yield f"  /{cmd.ljust(max_cmdlen)}  {desc}"

    yield "\b"
    yield "Keyboard shortcuts:"
    yield "  Ctrl+X Ctrl+E  Edit prompt in your editor"
    yield "  Ctrl+J         Insert a new line without executing the prompt"

    if incl_langtags:
        yield ""
        yield "To execute code with supported tools, use the following syntax:"
        yield "  /<langtag> <code>"
        yield ""
        yield "Example:"
        yield "  /sh echo hello"
        yield "  /python print('hello')"
        yield ""
        yield "Supported langtags:"
        for tool in get_tools():
            if tool.block_types:
                yield f"  - {tool.block_types[0]}" + (
                    f"  (alias: {', '.join(tool.block_types[1:])})"
                    if len(tool.block_types) > 1
                    else ""
                )


def help():
    for line in _gen_help():
        print(line)


def print_available_models() -> None:
    """Print all available models from all providers."""

    list_models(dynamic_fetch=True)


def get_user_commands() -> list[str]:
    """Returns a list of all user commands"""
    commands = [f"/{cmd}" for cmd in action_descriptions.keys()]

    # check if command is valid tooluse
    # TODO: check for registered tools instead of hardcoding
    commands.extend(["/python", "/shell"])

    return commands