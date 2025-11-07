import asyncio
import logging
import os
from contextlib import AsyncExitStack

import mcp.types as types  # Import all types
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from gptme.config import Config, get_config

logger = logging.getLogger(__name__)


def _is_connection_error(error: Exception) -> bool:
    """Check if error indicates MCP connection failure"""
    error_msg = str(error).lower()
    return any(
        phrase in error_msg
        for phrase in [
            "connection closed",
            "connection refused",
            "connection reset",
            "broken pipe",
            "pipe closed",
            "transport closed",
            "session closed",
            "server closed",
            "process terminated",
            "no such process",
        ]
    )


class MCPClient:
    """A client for interacting with MCP servers"""

    def __init__(self, config: Config | None = None):
        """Initialize the client with optional config"""
        self.config = config or get_config()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        logger.debug(f"Init - Loop ID: {id(self.loop)}")
        self.session: ClientSession | None = None
        self.tools: types.ListToolsResult | None = None
        self.stack: AsyncExitStack | None = None

    def _run_async(self, coro):
        """Run a coroutine in the event loop."""
        try:
            logger.debug(f"_run_async start - Loop ID: {id(self.loop)}")
            result = self.loop.run_until_complete(coro)
            logger.debug(f"_run_async end - Loop ID: {id(self.loop)}")
            return result
        except Exception as e:
            if _is_connection_error(e):
                logger.info(f"MCP connection error (will retry): {e}")
            else:
                logger.error(f"Unexpected MCP error: {e}")
            raise

    async def _read_stderr(self, stderr):
        """Read stderr without blocking the main flow"""
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                logger.debug(f"Server stderr: {line.decode().strip()}")
        except Exception as e:
            logger.debug(f"Stderr reader stopped: {e}")

    async def _setup_stdio_connection(
        self, server_params
    ) -> tuple[types.ListToolsResult, ClientSession]:
        """Set up stdio connection and maintain it"""
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()

        try:
            transport = await self.stack.enter_async_context(
                stdio_client(server_params)
            )
            read, write = transport

            csession = ClientSession(read, write)
            session = await self.stack.enter_async_context(csession)
            self.session = session  # Assign to self.session after the await

            if not self.session:
                raise RuntimeError("Failed to initialize session")

            await asyncio.wait_for(self.session.initialize(), timeout=5.0)
            tools = await asyncio.wait_for(self.session.list_tools(), timeout=10.0)
            self.tools = tools  # Assign after await

            if not self.tools:
                raise RuntimeError("Failed to get tools list")

            return (self.tools, self.session)
        except Exception:
            if self.stack:
                await self.stack.__aexit__(None, None, None)
                self.stack = None
            raise

    async def _setup_http_connection(
        self, url: str, headers: dict
    ) -> tuple[types.ListToolsResult, ClientSession]:
        """Set up HTTP connection and maintain it"""
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()

        try:
            transport = await self.stack.enter_async_context(
                streamablehttp_client(url, headers=headers)
            )
            read, write, _ = transport

            csession = ClientSession(read, write)
            session = await self.stack.enter_async_context(csession)
            self.session = session

            if not self.session:
                raise RuntimeError("Failed to initialize session")

            await asyncio.wait_for(self.session.initialize(), timeout=5.0)
            tools = await asyncio.wait_for(self.session.list_tools(), timeout=10.0)
            self.tools = tools

            if not self.tools:
                raise RuntimeError("Failed to get tools list")

            return (self.tools, self.session)
        except Exception:
            if self.stack:
                await self.stack.__aexit__(None, None, None)
                self.stack = None
            raise

    def connect(self, server_name: str) -> tuple[types.ListToolsResult, ClientSession]:
        """Connect to an MCP server by name"""
        if not self.config.mcp.enabled:
            raise RuntimeError("MCP is not enabled in config")

        server = next(
            (s for s in self.config.mcp.servers if s.name == server_name), None
        )
        if not server:
            raise ValueError(f"No MCP server config found for '{server_name}'")

        if server.is_http:
            # HTTP MCP server
            tools, session = self._run_async(
                self._setup_http_connection(server.url, server.headers)
            )
        else:
            # Stdio MCP server
            env = server.env or {}
            env.update(os.environ)

            params = StdioServerParameters(
                command=server.command, args=server.args, env=env
            )
            tools, session = self._run_async(self._setup_stdio_connection(params))

        logger.info(f"Tools: {tools}")
        return tools, session

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Synchronous tool call method"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        async def _call_tool():
            session = self.session
            if session is None:
                raise RuntimeError("Should not be None")

            result = await session.call_tool(tool_name, arguments)
            # Safely access content for logging
            if (
                hasattr(result, "content")
                and result.content
                and len(result.content) > 0
                and isinstance(result.content[0], types.TextContent)
            ):
                content_text = result.content[0].text
                logger.debug(f"result {content_text}")

            if hasattr(result, "content") and result.content:
                for content in result.content:
                    if (
                        hasattr(content, "type")
                        and content.type == "text"
                        and hasattr(content, "text")
                    ):
                        return content.text
            return str(result)

        return self._run_async(_call_tool())
