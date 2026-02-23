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

from ..hooks import confirm
from ..message import Message
from .base import (
    Parameter,
    ToolFormat,
    ToolSpec,
    ToolUse,
)
from .mcp_adapter import (
    add_mcp_root,
    disable_mcp_elicitation,
    enable_mcp_elicitation,
    get_mcp_elicitation_status,
    get_mcp_prompt,
    get_mcp_server_info,
    list_loaded_servers,
    list_mcp_prompts,
    list_mcp_resource_templates,
    list_mcp_resources,
    list_mcp_roots,
    load_mcp_server,
    read_mcp_resource,
    remove_mcp_root,
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
            config_override = command_args or None

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

        elif command.startswith("resources list"):
            # resources list <server-name>
            parts = command.split()
            if len(parts) < 3:
                yield Message("system", "Usage: resources list <server-name>")
                return

            server_name = parts[2]
            result = list_mcp_resources(server_name)
            yield Message("system", result)

        elif command.startswith("resources read"):
            # resources read <server-name> <uri>
            parts = command.split(maxsplit=3)
            if len(parts) < 4:
                yield Message("system", "Usage: resources read <server-name> <uri>")
                return

            server_name = parts[2]
            uri = parts[3]
            result = read_mcp_resource(server_name, uri)
            yield Message("system", result)

        elif command.startswith("templates list"):
            # templates list <server-name>
            parts = command.split()
            if len(parts) < 3:
                yield Message("system", "Usage: templates list <server-name>")
                return

            server_name = parts[2]
            result = list_mcp_resource_templates(server_name)
            yield Message("system", result)

        elif command.startswith("prompts list"):
            # prompts list <server-name>
            parts = command.split()
            if len(parts) < 3:
                yield Message("system", "Usage: prompts list <server-name>")
                return

            server_name = parts[2]
            result = list_mcp_prompts(server_name)
            yield Message("system", result)

        elif command.startswith("prompts get"):
            # prompts get <server-name> <prompt-name> [arguments-json]
            parts = command.split(maxsplit=4)
            if len(parts) < 4:
                yield Message(
                    "system",
                    "Usage: prompts get <server-name> <prompt-name> [arguments-json]",
                )
                return

            server_name = parts[2]
            prompt_name = parts[3]
            arguments = None
            if len(parts) > 4:
                try:
                    arguments = json.loads(parts[4])
                except json.JSONDecodeError as e:
                    yield Message("system", f"Invalid JSON arguments: {e}")
                    return

            result = get_mcp_prompt(server_name, prompt_name, arguments)
            yield Message("system", result)

        elif command.startswith("roots list"):
            # roots list [server-name]
            parts = command.split()
            roots_server_name = parts[2] if len(parts) > 2 else None
            result = list_mcp_roots(roots_server_name)
            yield Message("system", result)

        elif command.startswith("roots add"):
            # roots add <server-name> <uri> [name]
            parts = command.split(maxsplit=4)
            if len(parts) < 4:
                yield Message("system", "Usage: roots add <server-name> <uri> [name]")
                return

            add_server_name = parts[2]
            add_uri = parts[3]
            add_name = parts[4] if len(parts) > 4 else None
            result = add_mcp_root(add_server_name, add_uri, add_name)
            yield Message("system", result)

        elif command.startswith("roots remove"):
            # roots remove <server-name> <uri>
            parts = command.split()
            if len(parts) < 4:
                yield Message("system", "Usage: roots remove <server-name> <uri>")
                return

            remove_server_name = parts[2]
            remove_uri = parts[3]
            result = remove_mcp_root(remove_server_name, remove_uri)
            yield Message("system", result)

        elif command.startswith("elicitation enable"):
            # elicitation enable <server-name>
            parts = command.split()
            if len(parts) < 3:
                yield Message("system", "Usage: elicitation enable <server-name>")
                return

            elicit_server = parts[2]
            result = enable_mcp_elicitation(elicit_server)
            yield Message("system", result)

        elif command.startswith("elicitation disable"):
            # elicitation disable <server-name>
            parts = command.split()
            if len(parts) < 3:
                yield Message("system", "Usage: elicitation disable <server-name>")
                return

            elicit_server = parts[2]
            result = disable_mcp_elicitation(elicit_server)
            yield Message("system", result)

        elif command.startswith("elicitation status"):
            # elicitation status [server-name]
            parts = command.split()
            status_server: str | None = parts[2] if len(parts) > 2 else None
            result = get_mcp_elicitation_status(status_server)
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
                "  list - List loaded servers\n"
                "  resources list <server> - List resources from a server\n"
                "  resources read <server> <uri> - Read a resource\n"
                "  templates list <server> - List resource templates\n"
                "  prompts list <server> - List prompts from a server\n"
                "  prompts get <server> <name> [args] - Get a prompt\n"
                "  roots list [server] - List configured roots\n"
                "  roots add <server> <uri> [name] - Add a root\n"
                "  roots remove <server> <uri> - Remove a root\n"
                "  elicitation enable <server> - Enable elicitation for a server\n"
                "  elicitation disable <server> - Disable elicitation\n"
                "  elicitation status [server] - Show elicitation status",
            )

    except Exception as e:
        logger.error(f"Error executing MCP command: {e}")
        yield Message("system", f"Error: {e}")


def examples(tool_format: str) -> str:
    """Return example usage."""

    # Cast to ToolFormat type
    fmt: ToolFormat = tool_format  # type: ignore[assignment]
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
            ToolUse("mcp", [], "resources list sqlite").to_output(fmt),
            ToolUse("mcp", [], "resources read sqlite db://main/users").to_output(fmt),
            ToolUse("mcp", [], "templates list sqlite").to_output(fmt),
            ToolUse("mcp", [], "prompts list sqlite").to_output(fmt),
            ToolUse(
                "mcp", [], 'prompts get sqlite create-query {"table": "users"}'
            ).to_output(fmt),
            ToolUse("mcp", [], "roots list").to_output(fmt),
            ToolUse(
                "mcp", [], "roots add filesystem file:///home/user/project Project"
            ).to_output(fmt),
            ToolUse(
                "mcp", [], "roots remove filesystem file:///home/user/project"
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


def _cmd_mcp_resources_list(server_name: str) -> str:
    """List resources from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
    """
    return list_mcp_resources(server_name)


def _cmd_mcp_resources_read(server_name: str, uri: str) -> str:
    """Read a resource from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the resource to read
    """
    return read_mcp_resource(server_name, uri)


def _cmd_mcp_templates_list(server_name: str) -> str:
    """List resource templates from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
    """
    return list_mcp_resource_templates(server_name)


def _cmd_mcp_prompts_list(server_name: str) -> str:
    """List prompts from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
    """
    return list_mcp_prompts(server_name)


def _cmd_mcp_prompts_get(
    server_name: str, prompt_name: str, arguments: dict[str, str] | None = None
) -> str:
    """Get a specific prompt from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        prompt_name: Name of the prompt to retrieve
        arguments: Optional arguments for the prompt
    """
    return get_mcp_prompt(server_name, prompt_name, arguments)


def _cmd_mcp_roots_list(server_name: str | None = None) -> str:
    """List configured roots for MCP servers.

    Args:
        server_name: Optional server name to list roots for
    """
    return list_mcp_roots(server_name)


def _cmd_mcp_roots_add(server_name: str, uri: str, name: str | None = None) -> str:
    """Add a root to an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the root (e.g., file:///path/to/project)
        name: Optional human-readable name
    """
    return add_mcp_root(server_name, uri, name)


def _cmd_mcp_roots_remove(server_name: str, uri: str) -> str:
    """Remove a root from an MCP server.

    Args:
        server_name: Name of the loaded MCP server
        uri: URI of the root to remove
    """
    return remove_mcp_root(server_name, uri)


tool = ToolSpec(
    name="mcp",
    desc="Search, discover, and manage MCP servers",
    instructions="""
This tool allows you to search for MCP servers in various registries and dynamically load/unload them.

Once loaded, server tools are available as `<server-name>.<tool-name>`.

Search queries the Official MCP Registry (registry.modelcontextprotocol.io).

**Resource Commands** (for servers that expose resources):
- `resources list <server>` - List available resources from a loaded server
- `resources read <server> <uri>` - Read a specific resource by URI
- `templates list <server>` - List resource templates (parameterized resources)

**Prompt Commands** (for servers that expose prompts):
- `prompts list <server>` - List available prompts from a loaded server
- `prompts get <server> <name> [args]` - Get a specific prompt, optionally with JSON arguments

**Roots Commands** (for defining operational boundaries):
- `roots list [server]` - List configured roots (all servers if no server specified)
- `roots add <server> <uri> [name]` - Add a root to tell the server where it can operate
- `roots remove <server> <uri>` - Remove a root from a server

Roots are advisory URIs (file paths, HTTP URLs) that help servers understand workspace boundaries.
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
        "mcp resources list": _cmd_mcp_resources_list,
        "mcp resources read": _cmd_mcp_resources_read,
        "mcp templates list": _cmd_mcp_templates_list,
        "mcp prompts list": _cmd_mcp_prompts_list,
        "mcp prompts get": _cmd_mcp_prompts_get,
        "mcp roots list": _cmd_mcp_roots_list,
        "mcp roots add": _cmd_mcp_roots_add,
        "mcp roots remove": _cmd_mcp_roots_remove,
    },
    parameters=[
        Parameter(
            name="command",
            type="string",
            description="MCP management command (search, info, load, unload, list, resources list, resources read, templates list)",
            required=True,
        ),
    ],
)
__doc__ = tool.get_doc(__doc__)
