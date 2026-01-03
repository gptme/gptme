"""
Meta commands: help, setup, plugin, impersonate.
"""

from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..message import Message

from .base import CommandContext, command

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


@command("impersonate")
def cmd_impersonate(ctx: CommandContext) -> Generator["Message", None, None]:
    """Impersonate the assistant."""
    from ..message import Message  # fmt: skip
    from ..tools import execute_msg  # fmt: skip

    content = ctx.full_args if ctx.full_args else input("[impersonate] Assistant: ")
    msg = Message("assistant", content)
    yield msg
    yield from execute_msg(msg, confirm=lambda _: True)


@command("setup")
def cmd_setup(ctx: CommandContext) -> None:
    """Setup gptme with completions, configuration, and project setup."""
    from ..setup import setup

    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    setup()


@command("help")
def cmd_help(ctx: CommandContext) -> None:
    """Show help message."""
    ctx.manager.undo(1, quiet=True)
    ctx.manager.write()
    _help()


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
        from ..config import get_config
        from ..plugins import discover_plugins

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
    from ..config import get_config
    from ..plugins import (
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


def _gen_help(incl_langtags: bool = True) -> Generator[str, None, None]:
    """Generate help text."""
    yield "Available commands:"
    max_cmdlen = max(len(cmd) for cmd in COMMANDS)
    for cmd, desc in action_descriptions.items():
        yield f"  /{cmd.ljust(max_cmdlen)}  {desc}"

    yield "\b"
    yield "Keyboard shortcuts:"
    yield "  Ctrl+X Ctrl+E  Edit prompt in your editor"
    yield "  Ctrl+J         Insert a new line without executing the prompt"

    if incl_langtags:
        from ..tools import get_tools  # fmt: skip

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


def _help():
    """Print help message."""
    for line in _gen_help():
        print(line)
