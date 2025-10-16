import json
from collections.abc import Callable, Generator
from logging import getLogger

from gptme.config import Config

from ..mcp.client import MCPClient
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

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_config.name}: {e}")

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
