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
from .logmanager import (
    LogManager,
    delete_conversation,
    list_conversations,
    prepare_messages,
)
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
from .util.auto_naming import generate_llm_name
from .util.export import export_chat_to_html
from .util.useredit import edit_text_with_editor

logger = logging.getLogger(__name__)

Actions = Literal[
    "log",
    "undo",
    "edit",
    "rename",
    "fork",
    "delete",
    "tools",
    "model",
    "context",
    "replay",
    "impersonate",
    "summarize",
    "tokens",
    "export",
    "commit",
    "compact",
    "clear",
    "plugin",
    "setup",
    "restart",
    "help",
    "exit",
]

action_descriptions: dict[Actions, str] = {
    "undo": "Undo the last action",
    "log": "Show the conversation log",
    "edit": "Edit the conversation in your editor",
    "rename": "Rename the conversation",
    "fork": "Create a copy of the conversation",
    "delete": "Delete a conversation by ID (alias: /rm)",
    "summarize": "Summarize the conversation",
    "replay": "Replay tool operations",
    "export": "Export conversation as HTML",
    "model": "List or switch models",
    "tokens": "Show token usage and costs (alias: /cost)",
    "context": "Show context token breakdown",
    "tools": "Show available tools",
    "commit": "Ask assistant to git commit",
    "compact": "Compact the conversation",
    "impersonate": "Impersonate the assistant",
    "plugin": "Manage plugins",
    "clear": "Clear the terminal screen",
    "setup": "Setup gptme with completions and configuration",
    "restart": "Restart gptme process",
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
):
    """Decorator to register command handlers.

    Args:
        name: Command name (without leading /)
        aliases: Optional list of command aliases
        completer: Optional function for argument completion.
                   Takes (partial_arg, previous_args) and returns list of (completion, description) tuples.
    """

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
    rename(ctx.manager, new_name, ctx.confirm)


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


@command("summarize")
def cmd_summarize(ctx: CommandContext) -> None:
    """Summarize the conversation."""
    ctx.manager.undo(1, quiet=True)
    msgs = prepare_messages(ctx.manager.log.messages)
    msgs = [m for m in msgs if not m.hide]
    print_msg(llm.summarize(msgs))


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


@command("clear", aliases=["cls"])
def cmd_clear(ctx: CommandContext) -> None:
    """Clear the terminal screen."""
    ctx.manager.undo(1, quiet=True)
    # ANSI escape code to clear screen and move cursor to home position
    print("\033[2J\033[H", end="")


@command("exit")
def cmd_exit(ctx: CommandContext) -> None:
    """Exit the program."""
    from .hooks import HookType, trigger_hook

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
    from .tools.restart import _do_restart

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


def _complete_replay(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete replay command options."""
    completions: list[tuple[str, str]] = []
    options = [
        ("last", "Replay only the last assistant message"),
        ("all", "Replay all assistant messages"),
    ]
    for opt, desc in options:
        if opt.startswith(partial.lower()):
            completions.append((opt, desc))

    # Also suggest tool names
    for tool in get_tools():
        if tool.name.startswith(partial.lower()):
            completions.append((tool.name, f"Replay all {tool.name} operations"))

    return completions


@command("replay", completer=_complete_replay)
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


@command("tokens", aliases=["cost"])
def cmd_tokens(ctx: CommandContext) -> None:
    """Show token usage and costs.

    Shows session costs (current session) and conversation costs (all messages)
    when both are available. Falls back to approximation for old conversations.
    """
    from .util.cost_display import (
        display_costs,
        gather_conversation_costs,
        gather_session_costs,
    )

    ctx.manager.undo(1, quiet=True)

    session = gather_session_costs()
    conversation = gather_conversation_costs(ctx.manager.log.messages)
    display_costs(session, conversation)


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


@command("context")
def cmd_context(ctx: CommandContext) -> None:
    """Show context token usage breakdown."""
    from collections import defaultdict

    from .llm.models import get_default_model
    from .util import console
    from .util.tokens import len_tokens

    ctx.manager.undo(1, quiet=True)

    # Try to use the current model's tokenizer, fallback to gpt-4
    current_model = get_default_model()
    tokenizer_model = "gpt-4"
    is_approximate = True

    if current_model:
        # Use matching tokenizer for OpenAI models
        if current_model.provider == "openai" or (
            current_model.provider == "openrouter"
            and current_model.model.startswith("openai/")
        ):
            tokenizer_model = current_model.model.split("/")[-1]
            is_approximate = False

    # Track token counts by category
    by_role: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)

    # Analyze each message (including hidden, since they're sent to the model)
    for msg in ctx.manager.log.messages:
        content_tokens = len_tokens(msg.content, tokenizer_model)

        # Count by role
        by_role[msg.role] += content_tokens

        # Categorize content type
        # Check for tool uses
        tool_uses = list(ToolUse.iter_from_content(msg.content))
        if tool_uses:
            by_type["tool_use"] += content_tokens
        # Check for thinking blocks (Anthropic uses <thinking> tags)
        elif "<thinking>" in msg.content or "<think>" in msg.content:
            by_type["thinking"] += content_tokens
        else:
            by_type["message"] += content_tokens

    # Calculate totals
    total_tokens = sum(by_role.values())

    # Display breakdown
    console.log("[bold]Token Usage by Role:[/bold]")
    for role in ["system", "user", "assistant"]:
        tokens = by_role.get(role, 0)
        pct = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        console.log(f"  {role:10s}: {tokens:6,} ({pct:5.1f}%)")

    console.log("\n[bold]Token Usage by Type:[/bold]")
    for type_name, tokens in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        pct = (tokens / total_tokens * 100) if total_tokens > 0 else 0
        console.log(f"  {type_name:10s}: {tokens:6,} ({pct:5.1f}%)")

    console.log(f"\n[bold]Total Context:[/bold] {total_tokens:,} tokens")

    if is_approximate:
        console.log(f"[dim](approximate, using {tokenizer_model} tokenizer)[/dim]")


def _complete_model(partial: str, _prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete model names using dynamic fetching with caching.

    Uses the same model listing logic as gptme-util models list --simple.
    Caching is handled by get_model_list() in models.py.
    """
    from .llm.models import MODELS, PROVIDERS, get_default_model, get_model_list

    completions: list[tuple[str, str]] = []
    current = get_default_model()

    # Check if user is typing a provider prefix
    if "/" not in partial:
        # Show provider/ prefixes that match
        for provider in PROVIDERS:
            provider_prefix = f"{provider}/"
            if provider_prefix.startswith(partial) or provider.startswith(partial):
                # Count models for this provider
                model_count = len(MODELS.get(provider, {}))
                desc = f"{model_count} models" if model_count else "dynamic"
                completions.append((provider_prefix, desc))

    # Get full model list (cached in get_model_list)
    try:
        models = get_model_list(dynamic_fetch=True)
        for model_meta in models:
            full_name = model_meta.full
            if full_name.startswith(partial):
                is_current = current and current.full == full_name
                desc = "(current)" if is_current else ""
                completions.append((full_name, desc))
    except Exception:
        # Fall back to empty list on error (provider prefixes will still show)
        pass

    # Deduplicate while preserving order
    seen = set()
    unique_completions = []
    for item in completions:
        if item[0] not in seen:
            seen.add(item[0])
            unique_completions.append(item)

    return unique_completions[:30]  # Limit to 30 completions


@command("model", aliases=["models"], completer=_complete_model)
def cmd_model(ctx: CommandContext) -> None:
    """List or switch models."""
    ctx.manager.undo(1, quiet=True)
    if ctx.args:
        new_model = ctx.args[0]
        set_default_model(new_model)
        # Persist the model change to config so it survives restart/resume
        chat_config = ChatConfig.from_logdir(ctx.manager.logdir)
        chat_config.model = new_model
        chat_config.save()
        print(f"Set model to {new_model}")
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


def _complete_plugin(partial: str, prev_args: list[str]) -> list[tuple[str, str]]:
    """Complete plugin command subcommands and arguments."""
    completions: list[tuple[str, str]] = []

    if not prev_args:
        # Complete subcommand
        subcommands = [
            ("list", "Show all discovered plugins"),
            ("info", "Show details about a specific plugin"),
        ]
        for cmd, desc in subcommands:
            if cmd.startswith(partial):
                completions.append((cmd, desc))
    elif prev_args[0] == "info":
        # Complete plugin names
        from pathlib import Path

        from .config import get_config
        from .plugins import discover_plugins

        config = get_config()
        if config.project and config.project.plugins and config.project.plugins.paths:
            plugin_paths = [
                Path(p).expanduser().resolve() for p in config.project.plugins.paths
            ]
            plugins = discover_plugins(plugin_paths)
            for plugin in plugins:
                if plugin.name.startswith(partial):
                    completions.append((plugin.name, str(plugin.path)))

    return completions


@command("plugin", completer=_complete_plugin)
def cmd_plugin(ctx: CommandContext) -> None:
    """Manage plugins - list, show info, check installation status."""
    from pathlib import Path

    from .config import get_config
    from .plugins import (
        Plugin,
        detect_install_environment,
        discover_plugins,
        get_install_instructions,
    )

    ctx.manager.undo(1, quiet=True)

    config = get_config()

    if not ctx.args:
        print("Usage: /plugin <list|info> [name]")
        print("")
        print("Commands:")
        print("  list       Show all discovered plugins")
        print("  info NAME  Show details about a specific plugin")
        return

    subcommand = ctx.args[0]

    if subcommand == "list":
        # Get plugin paths from config
        plugin_paths = []
        if config.project and config.project.plugins and config.project.plugins.paths:
            plugin_paths = [
                Path(p).expanduser().resolve() for p in config.project.plugins.paths
            ]

        if not plugin_paths:
            print("No plugin paths configured.")
            print("")
            print("Add plugin paths to your gptme.toml:")
            print("")
            print("[plugins]")
            print('paths = ["path/to/plugin1", "path/to/plugin2"]')
            return

        plugins = discover_plugins(plugin_paths)

        if not plugins:
            print("No plugins discovered in configured paths.")
            return

        print(f"Discovered {len(plugins)} plugin(s):")
        for plugin in plugins:
            print(f"\n  {plugin.name}")
            print(f"    path: {plugin.path}")
            if plugin.tool_modules:
                print(f"    tools: {len(plugin.tool_modules)} module(s)")
            if plugin.hook_modules:
                print(f"    hooks: {len(plugin.hook_modules)} module(s)")
            if plugin.command_modules:
                print(f"    commands: {len(plugin.command_modules)} module(s)")

            # Check if plugin has dependencies
            pyproject_path = plugin.path.parent / "pyproject.toml"
            if not pyproject_path.exists():
                # Check one level up for src/ layout
                pyproject_path = plugin.path.parent.parent / "pyproject.toml"

            if pyproject_path.exists():
                print("    📦 Has dependencies (needs installation)")

    elif subcommand == "info":
        if len(ctx.args) < 2:
            print("Usage: /plugin info <plugin_name>")
            return

        plugin_name = ctx.args[1]

        # Get plugin paths from config
        plugin_paths = []
        if config.project and config.project.plugins and config.project.plugins.paths:
            plugin_paths = [
                Path(p).expanduser().resolve() for p in config.project.plugins.paths
            ]

        if not plugin_paths:
            print("No plugin paths configured.")
            return

        plugins = discover_plugins(plugin_paths)
        selected_plugin: Plugin | None = next(
            (p for p in plugins if p.name == plugin_name), None
        )

        if selected_plugin is None:
            print(f"Plugin '{plugin_name}' not found.")
            print(f"Available plugins: {', '.join(p.name for p in plugins)}")
            return

        print(f"Plugin: {selected_plugin.name}")
        print(f"  Path: {selected_plugin.path}")

        # Check if plugin has dependencies (pyproject.toml)
        pyproject_path = selected_plugin.path.parent / "pyproject.toml"
        if not pyproject_path.exists():
            # Check one level up for src/ layout
            pyproject_path = selected_plugin.path.parent.parent / "pyproject.toml"

        if pyproject_path.exists():
            print(f"\n  📦 Plugin package: {pyproject_path.parent}")

            # Show installation instructions
            env_type = detect_install_environment()
            install_cmd = get_install_instructions(pyproject_path.parent, env_type)
            print(f"\n  To install dependencies ({env_type} environment):")
            print(f"    {install_cmd}")
            print("")
            print("  Note: Installation must be done manually to respect your")
            print("        environment (pipx/uvx/venv/system).")

        if selected_plugin.tool_modules:
            print(f"\n  Tool modules ({len(selected_plugin.tool_modules)}):")
            for module in selected_plugin.tool_modules:
                print(f"    - {module}")

        if selected_plugin.hook_modules:
            print(f"\n  Hook modules ({len(selected_plugin.hook_modules)}):")
            for module in selected_plugin.hook_modules:
                print(f"    - {module}")

        if selected_plugin.command_modules:
            print(f"\n  Command modules ({len(selected_plugin.command_modules)}):")
            for module in selected_plugin.command_modules:
                print(f"    - {module}")

    else:
        print(f"Unknown subcommand: {subcommand}")
        print("Available commands: list, info")


def execute_cmd(msg: Message, log: LogManager, confirm: ConfirmFunc) -> bool:
    """Executes any user-command, returns True if command was executed."""
    from .util.content import is_message_command  # fmt: skip

    assert msg.role == "user"

    # if message starts with / treat as command
    # absolute paths dont trigger false positives by checking for single /
    if is_message_command(msg.content):
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
        yield from tooluse.execute(confirm, manager.log, manager.workspace)
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
    """Returns a list of all user commands, including tool-registered commands"""
    # Get all registered commands (includes built-in + tool-registered)
    return [f"/{cmd}" for cmd in _command_registry.keys()]


def init_commands() -> None:
    """Initialize plugin commands."""
    from pathlib import Path

    from .config import get_config
    from .plugins import register_plugin_commands

    config = get_config()
    if config.project and config.project.plugins and config.project.plugins.paths:
        register_plugin_commands(
            plugin_paths=[Path(p) for p in config.project.plugins.paths],
            enabled_plugins=config.project.plugins.enabled or None,
        )
