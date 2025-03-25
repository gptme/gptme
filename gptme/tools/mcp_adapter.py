from logging import getLogger
from collections.abc import Callable

from ..message import Message
from ..mcp.client import MCPClient
from .base import Parameter, ToolSpec

# Define ConfirmFunc type directly to avoid circular imports
ConfirmFunc = Callable[[str], bool]

logger = getLogger(__name__)


# Function to create MCP tools
def create_mcp_tools(config) -> list[ToolSpec]:
    """Create tool specs for all MCP tools from the config"""

    tool_specs = []
    servers = {}

    # Skip if MCP is not enabled
    if not config.mcp.enabled:
        return tool_specs

    # Initialize connections to all servers
    for server_config in config.mcp.servers:
        try:
            client = MCPClient(config=config)

            # Connect to server
            tools, session = client.connect(server_config.name)

            # Store the connection
            servers[server_config.name] = {
                "client": client,
                "tools": tools,
                "session": session,
            }

            breakpoint()
            # Create tool specs for each tool
            for mcp_tool in tools.tools:
                # Extract parameters
                parameters = []
                breakpoint()
                if hasattr(mcp_tool, "parameters") and mcp_tool.parameters:
                    for (
                        param_name,
                        param_schema,
                    ) in mcp_tool.parameters.properties.items():
                        parameters.append(
                            Parameter(
                                name=param_name,
                                description=param_schema.description or "",
                                type=param_schema.type or "string",
                                required=param_name
                                in (mcp_tool.parameters.required or []),
                            )
                        )

                # Create a tool spec with a simple execute function
                tool_spec = ToolSpec(
                    name=f"{server_config.name}.{mcp_tool.name}",
                    desc=f"[{server_config.name}] {mcp_tool.description}",
                    parameters=parameters,
                    execute=create_mcp_execute_function(mcp_tool.name, client),
                    available=True,
                )

                breakpoint()
                tool_specs.append(tool_spec)

        except Exception as e:
            logger.error(f"Failed to connect to MCP server {server_config.name}: {e}")

    return tool_specs


# Function to create execute function for a specific MCP tool
def create_mcp_execute_function(tool_name, client):
    """Create an execute function for an MCP tool"""

    def execute(content=None, args=None, kwargs=None, confirm=None):
        """Execute an MCP tool with confirmation"""
        try:
            # Format the command and parameters for display
            formatted_args = ""
            if kwargs and len(kwargs) > 0:
                formatted_args = ", ".join(f"{k}={v}" for k, v in kwargs.items())

            # Show preview and get confirmation
            if confirm is not None:
                confirmation_message = f"Run MCP tool '{tool_name}'"
                if formatted_args:
                    confirmation_message += f" with arguments: {formatted_args}"
                confirmation_message += "?"

                # Exit if not confirmed
                if not confirm(confirmation_message):
                    return Message("system", "Tool execution cancelled")

            # Execute the tool
            result = client.call_tool(tool_name, kwargs or {})
            return Message("system", result)
        except Exception as e:
            logger.error(f"Error executing MCP tool {tool_name}: {e}")
            return Message("system", f"Error executing tool: {e}")

    return execute
