"""
MCP server discovery and management tool.

Allows searching for MCP servers in registries and dynamically loading/unloading them.

Available Commands:
- ``/mcp search [query]`` - Search for MCP servers across all registries
- ``/mcp info <server-name>`` - Get detailed information about a specific server
- ``/mcp load <server-name> [config-json]`` - Dynamically load an MCP server into the current session
- ``/mcp unload <server-name>`` - Unload a previously loaded MCP server
- ``/mcp list`` - List all currently configured and loaded MCP servers

The search command queries the Official MCP Registry (registry.modelcontextprotocol.io).
Once loaded, server tools are available as ``<server-name>.<tool-name>``.
"""

import json
from collections.abc import Generator
from logging import getLogger

from ..message import Message
from .base import (
    ConfirmFunc,
    Parameter,
    ToolFormat,
    ToolSpec,
    ToolUse,
)
from .mcp_adapter import (
    get_mcp_server_info,
    list_loaded_servers,
    load_mcp_server,
    search_mcp_servers,
    unload_mcp_server,
)

logger = getLogger(__name__)


def _get_local_server_info(name: str) -> str | None:
    """Get info about a locally configured server, or None if not found.

    Returns formatted info string if server is configured locally, None otherwise.
    """
    from ..config import get_config

    config = get_config()
    local_server = next((s for s in config.mcp.servers if s.name == name), None)

    if not local_server:
        return None

    result = f"# {local_server.name} (configured locally)\n\n"
    result += f"**Type:** {'HTTP' if local_server.is_http else 'stdio'}\n"
    result += f"**Enabled:** {'Yes' if local_server.enabled else 'No'}\n\n"

    if local_server.is_http:
        result += f"**URL:** {local_server.url}\n"
        if local_server.headers:
            result += f"**Headers:** {len(local_server.headers)} configured\n"
    else:
        result += f"**Command:** {local_server.command}\n"
        if local_server.args:
            result += f"**Args:** {', '.join(local_server.args)}\n"

    return result


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

            # First check if server is configured locally
            local_info = _get_local_server_info(name)

            if local_info:
                yield Message("system", local_info)
            else:
                # Not found locally, search registries
                result = get_mcp_server_info(name)
                if "not found" in result.lower():
                    result = f"Server '{name}' not configured locally.\n\n" + result
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

    # Cast to ToolFormat type
    fmt: ToolFormat = tool_format  # type: ignore
    return "\n\n".join(
        [
            ToolUse("mcp", [], "search sqlite").to_output(fmt),
            ToolUse("mcp", [], "info sqlite").to_output(fmt),
            ToolUse("mcp", [], "load sqlite").to_output(fmt),
            ToolUse("mcp", [], "list").to_output(fmt),
            ToolUse("mcp", [], "unload sqlite").to_output(fmt),
            ToolUse(
                "mcp",
                [],
                'load my-server\n{"command": "uvx", "args": ["my-mcp-server", "--option"]}',
            ).to_output(fmt),
        ]
    )


def _cmd_mcp_search(query: str = "", registry: str = "all", limit: int = 10) -> str:
    """Search for MCP servers.

    Args:
        query: Search query
        registry: Registry to search (all, official, mcp.so)
        limit: Maximum number of results to return
    """
    return search_mcp_servers(query, registry, limit)


def _cmd_mcp_info(name: str) -> str:
    """Get info about an MCP server."""
    local_info = _get_local_server_info(name)

    if local_info:
        return local_info
    else:
        result = get_mcp_server_info(name)
        if "not found" in result.lower():
            result = f"Server '{name}' not configured locally.\n\n" + result
        return result


def _cmd_mcp_list() -> str:
    """List loaded MCP servers."""
    return list_loaded_servers()


def _cmd_mcp_load(name: str, config_json: str = "") -> str:
    """Load an MCP server."""
    config_override = None
    if config_json:
        try:
            config_override = json.loads(config_json)
        except json.JSONDecodeError as e:
            return f"Error parsing config JSON: {e}"

    return load_mcp_server(name, config_override)


def _cmd_mcp_unload(name: str) -> str:
    """Unload an MCP server."""
    return unload_mcp_server(name)


tool = ToolSpec(
    name="mcp",
    desc="Search, discover, and manage MCP servers",
    instructions="""
This tool allows you to search for MCP servers in various registries and dynamically load/unload them.

Once loaded, server tools are available as `<server-name>.<tool-name>`.

Search queries the Official MCP Registry (registry.modelcontextprotocol.io).
""".strip(),
    examples=examples,
    execute=execute_mcp,
    block_types=["mcp"],
    commands={
        "mcp search": _cmd_mcp_search,
        "mcp info": _cmd_mcp_info,
        "mcp list": _cmd_mcp_list,
        "mcp load": _cmd_mcp_load,
        "mcp unload": _cmd_mcp_unload,
    },
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
