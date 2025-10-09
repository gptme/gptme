"""
MCP server discovery and management tool.

Allows searching for MCP servers in registries and dynamically loading/unloading them.
"""

import json
from collections.abc import Generator
from logging import getLogger

from .base import ConfirmFunc, Parameter, ToolSpec, ToolUse
from .mcp_adapter import (
    get_mcp_server_info,
    list_loaded_servers,
    load_mcp_server,
    search_mcp_servers,
    unload_mcp_server,
)
from ..message import Message

logger = getLogger(__name__)


def execute_mcp(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute MCP management commands."""
    if not code:
        yield Message("system", "No command provided")
        return

    try:
        # Parse command and arguments
        lines = code.strip().split("\n")
        command = lines[0].strip()

        # Get additional arguments if provided
        command_args = {}
        if len(lines) > 1:
            # Try to parse as JSON
            try:
                command_args = json.loads("\n".join(lines[1:]))
            except json.JSONDecodeError:
                pass

        if command.startswith("search"):
            # search [query] [--registry=all] [--limit=10]
            parts = command.split()
            query = parts[1] if len(parts) > 1 else ""
            registry = command_args.get("registry", "all")
            limit = int(command_args.get("limit", "10"))

            result = search_mcp_servers(query, registry, limit)
            yield Message("system", result)

        elif command.startswith("info"):
            # info <server-name>
            parts = command.split()
            if len(parts) < 2:
                yield Message("system", "Usage: info <server-name>")
                return

            name = parts[1]
            result = get_mcp_server_info(name)
            yield Message("system", result)

        elif command.startswith("load"):
            # load <server-name> [config-override]
            parts = command.split()
            if len(parts) < 2:
                yield Message("system", "Usage: load <server-name> [config-override]")
                return

            name = parts[1]
            config_override = command_args if command_args else None

            # Ask for confirmation
            if not confirm(f"Load MCP server '{name}'?"):
                yield Message("system", "Cancelled")
                return

            result = load_mcp_server(name, config_override)
            yield Message("system", result)

        elif command.startswith("unload"):
            # unload <server-name>
            parts = command.split()
            if len(parts) < 2:
                yield Message("system", "Usage: unload <server-name>")
                return

            name = parts[1]

            # Ask for confirmation
            if not confirm(f"Unload MCP server '{name}'?"):
                yield Message("system", "Cancelled")
                return

            result = unload_mcp_server(name)
            yield Message("system", result)

        elif command == "list":
            # list
            result = list_loaded_servers()
            yield Message("system", result)

        else:
            yield Message(
                "system",
                f"Unknown MCP command: {command}\n\n"
                "Available commands:\n"
                "  search [query] - Search MCP registries\n"
                "  info <name> - Get detailed server information\n"
                "  load <name> - Dynamically load a server\n"
                "  unload <name> - Unload a server\n"
                "  list - List loaded servers",
            )

    except Exception as e:
        logger.error(f"Error executing MCP command: {e}")
        yield Message("system", f"Error: {e}")


def examples(tool_format: str) -> str:
    """Return example usage."""
    from .base import ToolFormat

    # Cast to ToolFormat type
    fmt: ToolFormat = tool_format  # type: ignore
    return "\n\n".join(
        [
            ToolUse("mcp", [], "search database").to_output(fmt),
            ToolUse("mcp", [], "info sqlite").to_output(fmt),
            ToolUse("mcp", [], "load sqlite").to_output(fmt),
            ToolUse("mcp", [], "list").to_output(fmt),
            ToolUse("mcp", [], "unload sqlite").to_output(fmt),
        ]
    )


__doc__ = """
MCP Server Discovery and Management

This tool allows you to search for MCP servers in various registries and dynamically load/unload them during a conversation.

Available Commands:
- `search [query]` - Search for MCP servers across all registries
  - Optional JSON config: `{"registry": "official|mcp.so|all", "limit": 10}`
- `info <server-name>` - Get detailed information about a specific server
- `load <server-name>` - Dynamically load an MCP server into the current session
  - Optional JSON config override: `{"command": "...", "args": [...], "url": "..."}`
- `unload <server-name>` - Unload a previously loaded MCP server
- `list` - List all currently configured and loaded MCP servers

The search command queries:
- Official MCP Registry (registry.modelcontextprotocol.io)
- MCP.so directory
- Other configured registries

Examples:

Search for database-related servers:
```mcp
search database
```

Get detailed info about a server:
```mcp
info sqlite
```

Load a server dynamically:
```mcp
load sqlite
```

Load with custom config:
```mcp
load my-server
{"command": "uvx", "args": ["my-mcp-server", "--option"]}
```

List all loaded servers:
```mcp
list
```

Unload a server:
```mcp
unload sqlite
```

Once loaded, the server's tools will be available as `<server-name>.<tool-name>` in the conversation.
"""

tool = ToolSpec(
    name="mcp",
    desc="Search, discover, and manage MCP servers",
    instructions=__doc__,
    examples=examples,
    execute=execute_mcp,
    block_types=["mcp"],
    parameters=[
        Parameter(
            name="command",
            type="string",
            description="MCP management command (search, info, load, unload, list)",
            required=True,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
