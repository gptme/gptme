from dataclasses import dataclass
import logging

from gptme.mcp import MCPClient
from gptme.tools.base import ToolSpec, Parameter
from gptme.message import Message

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConnection:
    """Represents a connection to an MCP server"""

    name: str
    client: MCPClient
    session: object | None = None


class MCPToolAdapter:
    def __init__(self, config):
        self.config = config
        self.servers: dict[str, MCPServerConnection] = {}

    def initialize(self) -> None:
        """Initialize connections to all enabled MCP servers"""
        if not self.config.mcp.enabled:
            return

        for server_config in self.config.mcp.servers:
            if not server_config.enabled:
                continue

            try:
                client = MCPClient(self.config)
                tools, session = client.connect(server_config.name)

                # Store the server connection
                self.servers[server_config.name] = MCPServerConnection(
                    name=server_config.name, client=client, session=session
                )

                logger.info(f"Connected to MCP server: {server_config.name}")

            except Exception as e:
                logger.error(
                    f"Failed to connect to MCP server {server_config.name}: {e}"
                )
                continue

    def get_tool_specs(self) -> list[ToolSpec]:
        """Convert MCP tools to ToolSpec format"""
        tool_specs: list[ToolSpec] = []

        for server_name, connection in self.servers.items():
            if not connection.client.tools:
                continue

            for mcp_tool in connection.client.tools.tools:
                # Convert MCP parameters to ToolSpec parameters
                parameters = []
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

                # Create execute function that calls the MCP tool
                def make_execute(tool_name, client):
                    def execute(content=None, args=None, kwargs=None, confirm=None):
                        try:
                            result = client.call_tool(tool_name, kwargs or {})
                            return Message("system", result)
                        except Exception as e:
                            logger.error(f"Error executing MCP tool {tool_name}: {e}")
                            return Message("system", f"Error executing tool: {e}")

                    return execute

                # Create ToolSpec with server prefix
                tool_specs.append(
                    ToolSpec(
                        name=f"{server_name}.{mcp_tool.name}",
                        desc=f"[{server_name}] {mcp_tool.description}",
                        parameters=parameters,
                        execute=make_execute(mcp_tool.name, connection.client),
                    )
                )

                logger.debug(f"Added MCP tool: {server_name}.{mcp_tool.name}")

        return tool_specs

    async def cleanup(self):
        """Clean up all MCP server connections"""
        raise NotImplementedError("Do not run this cleanup")
        for connection in self.servers.values():
            if connection.client and connection.client.stack:
                await connection.client.stack.__aexit__(None, None, None)
