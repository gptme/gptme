import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from gptme.config import Config, get_config
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
import mcp.types as types  # Import all types

logger = logging.getLogger(__name__)


class MCPClient:
    """A client for interacting with MCP servers"""

    def __init__(self, config: Config=None):
        """Initialize the client with optional config"""
        self.config = config or get_config()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        logger.debug(f"Init - Loop ID: {id(self.loop)}")
        self.session = None
        self.tools = None
        self.stack = None

    def _run_async(self, coro):
        """Run a coroutine in the event loop."""
        try:
            logger.debug(f"_run_async start - Loop ID: {id(self.loop)}")
            result = self.loop.run_until_complete(coro)
            logger.debug(f"_run_async end - Loop ID: {id(self.loop)}")
            return result
        except RuntimeError as e:
            if "no running event loop" in str(e):
                # Create a new loop if needed
                new_loop = asyncio.new_event_loop()
                logger.debug(f"Created new loop - Loop ID: {id(new_loop)}")
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
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

    async def _setup_connection(self, server_params):
        """Set up the connection and maintain it"""
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()
        
        try:
            transport = await self.stack.enter_async_context(stdio_client(server_params))
            read, write = transport
            
            csession = ClientSession(read, write)
            self.session = await self.stack.enter_async_context(csession)
            
            await asyncio.wait_for(self.session.initialize(), timeout=5.0)
            self.tools = await asyncio.wait_for(self.session.list_tools(), timeout=10.0)
            return self.tools, self.session
        except Exception:
            if self.stack:
                await self.stack.__aexit__(None, None, None)
                self.stack = None
            raise

    def connect(self, server_name: str) -> types.ListToolsResult:
        """Connect to an MCP server by name"""
        if not self.config.mcp.enabled:
            raise RuntimeError("MCP is not enabled in config")

        server = next(
            (s for s in self.config.mcp.servers if s.name == server_name), None
        )
        if not server:
            raise ValueError(f"No MCP server config found for '{server_name}'")

        params = StdioServerParameters(
            command=server.command, args=server.args, env=server.env
        )

        tools, session = self._run_async(self._setup_connection(params))
        logger.info(f"Tools: {tools}")
        return tools, session

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Synchronous tool call method"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")

        async def _call_tool():
            result = await self.session.call_tool(tool_name, arguments)
            logger.debug(f"result {result.content[0].text}")
            if hasattr(result, "content") and result.content:
                for content in result.content:
                    if content.type == "text":
                        return content.text
            return str(result)

        return self._run_async(_call_tool())
