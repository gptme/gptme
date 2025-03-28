from logging import getLogger
from collections.abc import Callable
import json

from ..message import Message
from ..mcp.client import MCPClient
from .base import Parameter, ToolSpec, ToolUse

# Define ConfirmFunc type directly to avoid circular imports
ConfirmFunc = Callable[[str], bool]

logger = getLogger(__name__)

# Add type annotation for tool_specs
tool_specs: list[ToolSpec] = []


# Function to create MCP tools
def create_mcp_tools(config) -> list[ToolSpec]:
    """Create tool specs for all MCP tools from the config"""

    tool_specs: list[ToolSpec] = []
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

                name = f"{server_config.name}_{mcp_tool.name}"
                tool_spec = ToolSpec(
                    name=name,
                    desc=f"[{server_config.name}] {mcp_tool.description}",
                    parameters=parameters,
                    execute=create_mcp_execute_function(mcp_tool.name, client),
                    available=True,
                    examples=lambda tool_format: ToolUse(
                        name, [], example_str
                    ).to_output(tool_format),
                    block_types=[name],
                )

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
            # Get the tool definition from the client
            tool_def = next(
                (tool for tool in client.tools.tools if tool.name == tool_name), None
            )

            # Try to parse content as JSON if it's not already kwargs
            if content and not kwargs:
                try:
                    kwargs = json.loads(content)
                except json.JSONDecodeError as err:
                    # Add proper error chaining with 'from'
                    if tool_def and tool_def.parameters:
                        example = json.dumps(
                            dict((p.name, f"<{p.type}>") for p in tool_def.parameters)
                        )
                    else:
                        example = '{"parameter": "value"}'
                    raise ValueError(
                        f"Content must be a valid JSON object with parameters. Example: {example}"
                    ) from err

            # Format the command and parameters for display
            formatted_args = ""
            if kwargs and len(kwargs) > 0:
                formatted_args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())

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

            return Message("system", error_msg)

    return execute
