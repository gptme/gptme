import asyncio
import json
from collections.abc import Callable, Generator
from logging import getLogger

from gptme.config import Config, MCPServerConfig, get_config, set_config

from ..mcp.client import MCPClient
from ..mcp.registry import MCPRegistry, format_server_details, format_server_list
from ..message import Message
from ..util.ask_execute import execute_with_confirmation
from .base import (
    ExecuteFunc,
    Parameter,
    ToolSpec,
    ToolUse,
)

# Define ConfirmFunc type directly to avoid circular imports
ConfirmFunc = Callable[[str], bool]

logger = getLogger(__name__)

# Global storage for MCP clients
_mcp_clients: dict[str, MCPClient] = {}

# Add type annotation for tool_specs
tool_specs: list[ToolSpec] = []

# Global registry instance
_registry = MCPRegistry()

# Cache of dynamically loaded servers
_dynamic_servers: dict[str, MCPClient] = {}


def _restart_mcp_client(server_name: str, config: Config) -> MCPClient:
    """Restart an MCP client by reconnecting to the server"""
    logger.info(f"Restarting MCP client for server: {server_name}")

    # Get existing client if any
    old_client = _mcp_clients.get(server_name)

    # Close old client if it exists
    if old_client is not None:
        try:
            # Clean up the old client
            if old_client.stack:
                old_client.loop.run_until_complete(
                    old_client.stack.__aexit__(None, None, None)
                )
            old_client.loop.close()
            logger.debug(f"Closed old MCP client for {server_name}")
        except Exception as e:
            logger.debug(f"Error closing old MCP client: {e}")

    # Create new client and reconnect
    new_client = MCPClient(config=config)
    tools, session = new_client.connect(server_name)

    # Store the new client
    _mcp_clients[server_name] = new_client

    logger.info(f"Successfully restarted MCP client for {server_name}")
    return new_client


def _call_mcp_tool_with_retry(
    server_name: str,
    tool_name: str,
    arguments: dict,
    config: Config,
    max_retries: int = 1,
) -> str:
    """Call an MCP tool with automatic retry on connection failures"""
    from ..mcp.client import _is_connection_error

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            # Get the client for this server
            client = _mcp_clients.get(server_name)
            if client is None:
                raise RuntimeError(f"No MCP client found for server: {server_name}")

            # Call the tool
            return client.call_tool(tool_name, arguments)

        except Exception as e:
            last_error = e

            if _is_connection_error(e) and attempt < max_retries:
                logger.info(f"MCP connection failed for {server_name}, restarting...")
                _restart_mcp_client(server_name, config)
                continue
            else:
                break

    # last_error will never be None here since we only break after setting it
    assert last_error is not None
    raise last_error


# Function to create MCP tools
def create_mcp_tools(config: Config) -> list[ToolSpec]:
    """Create tool specs for all MCP tools from the config"""

    tool_specs: list[ToolSpec] = []

    # Skip if MCP is not enabled
    if not config.mcp.enabled:
        return tool_specs

    # Initialize connections to all servers
    for server_config in config.mcp.servers:
        try:
            client = MCPClient(config=config)

            # Connect to server
            tools, session = client.connect(server_config.name)

            # Store the client globally for restart capability
            _mcp_clients[server_config.name] = client

            # Create tool specs for each tool
            for mcp_tool in tools.tools:
                # Extract parameters
                parameters = []
                # Check if the tool has inputSchema with properties
                if (
                    hasattr(mcp_tool, "inputSchema")
                    and isinstance(mcp_tool.inputSchema, dict)
                    and "properties" in mcp_tool.inputSchema
                ):
                    required_params = mcp_tool.inputSchema.get("required", [])
                    for param_name, param_schema in mcp_tool.inputSchema[
                        "properties"
                    ].items():
                        parameters.append(
                            Parameter(
                                name=param_name,
                                description=param_schema.get("description", ""),
                                type=param_schema.get("type", "string"),
                                required=param_name in required_params,
                            )
                        )

                # Add example usage in the correct format
                example = {
                    param.name: f"<{param.type}>"
                    for param in parameters
                    if param.required
                }
                example_str = json.dumps(example, indent=2)

                name = f"{server_config.name}.{mcp_tool.name}"

                def make_examples(
                    tool_name: str, example_content: str
                ) -> Callable[[str], str]:
                    return lambda tool_format: ToolUse(
                        tool_name, [], example_content
                    ).to_output(tool_format)  # type: ignore[arg-type]

                tool_spec = ToolSpec(
                    name=name,
                    desc=f"[{server_config.name}] {mcp_tool.description}",
                    parameters=parameters,
                    execute=create_mcp_execute_function(
                        mcp_tool.name, server_config.name, config
                    ),
                    available=True,
                    examples=make_examples(name, example_str),
                    block_types=[name],
                    is_mcp=True,
                )

                tool_specs.append(tool_spec)

        except (Exception, asyncio.CancelledError) as e:
            import traceback

            error_details = traceback.format_exc()
            logger.error(
                f"Failed to connect to MCP server {server_config.name}: {e}\n{error_details}"
            )

    return tool_specs


# Function to create execute function for a specific MCP tool
def create_mcp_execute_function(
    tool_name: str, server_name: str, config: Config
) -> ExecuteFunc:
    """Create an execute function for an MCP tool"""

    def preview_mcp(content: str, tool_name: str = tool_name) -> str | None:
        """Prepare preview content for MCP tool execution."""
        try:
            if content:
                # Try to parse and format the JSON parameters
                params = json.loads(content)
                return json.dumps(params, indent=2)
            return None
        except json.JSONDecodeError:
            return content  # Return as-is if not valid JSON

    def execute_mcp_impl(
        content: str, tool_name: str, confirm: ConfirmFunc
    ) -> Generator[Message, None, None]:
        """Actual MCP tool implementation."""
        try:
            # Get the client for getting tool definition
            client = _mcp_clients.get(server_name)
            if client is None:
                raise RuntimeError(f"No MCP client found for server: {server_name}")

            # Get the tool definition from the client
            tool_def = None
            if client.tools is not None:
                tool_def = next(
                    (tool for tool in client.tools.tools if tool.name == tool_name),
                    None,
                )

            # Parse content as JSON
            try:
                kwargs = json.loads(content) if content else {}
            except json.JSONDecodeError as err:
                if tool_def and tool_def.inputSchema:
                    example = json.dumps(
                        {
                            prop_name: f"<{prop_info.get('type', 'string')}>"
                            for prop_name, prop_info in tool_def.inputSchema.get(
                                "properties", {}
                            ).items()
                        },
                        indent=2,
                    )
                else:
                    example = '{\n  "parameter": "value"\n}'
                raise ValueError(
                    f"Content must be a valid JSON object with parameters. Example:\n{example}"
                ) from err

            # Execute the tool with retry on connection failures
            result = _call_mcp_tool_with_retry(server_name, tool_name, kwargs, config)
            yield Message("system", result)
        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {e}")

            # Provide a helpful error message with parameter information
            error_msg = f"Error executing tool: {e}\n\n"
            if tool_def and tool_def.inputSchema:
                error_msg += "Expected parameters:\n"
                for param_name, param_info in tool_def.inputSchema.get(
                    "properties", {}
                ).items():
                    required = (
                        "Required"
                        if param_name in tool_def.inputSchema.get("required", [])
                        else "Optional"
                    )
                    desc = param_info.get("description", "No description")
                    error_msg += f"- {param_name}: {desc} ({required})\n"

            yield Message("system", error_msg)

    def execute(
        code: str | None,
        args: list[str] | None,
        kwargs: dict[str, str] | None,
        confirm: ConfirmFunc,
    ):
        """Execute an MCP tool with confirmation"""
        if not code:
            yield Message("system", "No parameters provided")
            return

        # Use execute_with_confirmation like save tool does
        yield from execute_with_confirmation(
            code,
            args,
            kwargs,
            confirm,
            execute_fn=lambda content, *_: execute_mcp_impl(
                content, tool_name, confirm
            ),
            get_path_fn=lambda *_: None,  # MCP tools don't have paths
            preview_fn=lambda content, *_: preview_mcp(content),
            preview_lang="json",
            confirm_msg=f"Execute MCP tool '{tool_name}'?",
            allow_edit=True,
        )

    return execute


def search_mcp_servers(query: str = "", registry: str = "all", limit: int = 10) -> str:
    """
    Search for MCP servers in registries.

    Args:
        query: Search query (searches name, description, tags)
        registry: Which registry to search ('all', 'official', 'mcp.so')
        limit: Maximum number of results

    Returns:
        Formatted list of servers
    """
    if registry == "all":
        results = _registry.search_all(query, limit)
    elif registry == "official":
        results = _registry.search_official_registry(query, limit)
    elif registry == "mcp.so":
        results = _registry.search_mcp_so(query, limit)
    else:
        return f"Unknown registry: {registry}. Use 'all', 'official', or 'mcp.so'."

    return format_server_list(results)


def get_mcp_server_info(name: str) -> str:
    """
    Get detailed information about a specific MCP server.

    Args:
        name: Server name

    Returns:
        Formatted server details
    """
    server = _registry.get_server_details(name)
    if not server:
        return f"Server '{name}' not found in any registry."

    return format_server_details(server)


def load_mcp_server(name: str, config_override: dict | None = None) -> str:
    """
    Dynamically load an MCP server during the session.

    Args:
        name: Server name (will search registries if not in config)
        config_override: Optional config overrides (command, args, url, etc.)

    Returns:
        Status message
    """
    config = get_config()

    # Check if server already loaded
    if name in _dynamic_servers:
        return f"Server '{name}' is already loaded."

    # Check if server is in config
    server_config = next((s for s in config.mcp.servers if s.name == name), None)

    # If not in config, try to find in registry
    if not server_config:
        server_info = _registry.get_server_details(name)
        if not server_info:
            return f"Server '{name}' not found in config or registries."

        # Create config from registry info
        server_config = MCPServerConfig(
            name=server_info.name,
            enabled=True,
            command=server_info.command,
            args=server_info.args,
            url=server_info.url,
        )

    # Apply config overrides
    if config_override:
        if "command" in config_override:
            server_config.command = config_override["command"]
        if "args" in config_override:
            server_config.args = config_override["args"]
        if "url" in config_override:
            server_config.url = config_override["url"]
        if "env" in config_override:
            server_config.env = config_override["env"]

    try:
        # Create client and connect
        client = MCPClient(config=config)
        tools, session = client.connect(name)

        # Store in dynamic servers
        _dynamic_servers[name] = client

        # Add to config
        if server_config not in config.mcp.servers:
            config.mcp.servers.append(server_config)
            set_config(config)

        tool_names = [tool.name for tool in tools.tools]
        return f"Successfully loaded server '{name}' with {len(tool_names)} tools: {', '.join(tool_names)}"

    except Exception as e:
        logger.error(f"Failed to load server '{name}': {e}")
        return f"Failed to load server '{name}': {e}"


def unload_mcp_server(name: str) -> str:
    """
    Unload a dynamically loaded MCP server.

    Args:
        name: Server name

    Returns:
        Status message
    """
    if name not in _dynamic_servers:
        return f"Server '{name}' is not loaded."

    # Remove from dynamic servers
    del _dynamic_servers[name]

    # Optionally disable in config (but don't remove)
    config = get_config()
    server_config = next((s for s in config.mcp.servers if s.name == name), None)
    if server_config:
        server_config.enabled = False
        set_config(config)

    return f"Successfully unloaded server '{name}'."


def list_loaded_servers() -> str:
    """
    List all currently loaded MCP servers.

    Returns:
        Formatted list of loaded servers
    """
    config = get_config()

    if not config.mcp.servers:
        return "No MCP servers configured."

    output = ["# Loaded MCP Servers\n"]

    for server in config.mcp.servers:
        status = "✓ enabled" if server.enabled else "✗ disabled"
        dynamic = " (dynamic)" if server.name in _dynamic_servers else ""
        output.append(f"- **{server.name}** {status}{dynamic}")
        if server.command:
            output.append(f"  Command: `{server.command}`")
        elif server.url:
            output.append(f"  URL: `{server.url}`")
        output.append("")

    return "\n".join(output)
