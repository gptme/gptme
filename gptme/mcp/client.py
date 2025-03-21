import asyncio
from typing import Optional
from contextlib import AsyncExitStack
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import logging
import json
import sys

logger = logging.getLogger(__name__)

class MCPClient:
    """A client for interacting with MCP servers"""
    
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
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

    async def _connect(self, server_params: StdioServerParameters):
        logger.debug("Starting async connection...")
        
        process = await asyncio.create_subprocess_exec(
            server_params.command,
            *server_params.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        logger.debug("Process started")
        
        # Start stderr reader in background
        stderr_task = asyncio.create_task(self._read_stderr(process.stderr))
        
        try:
            # Try the normal MCP flow immediately
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
            read, write = stdio_transport
            
            logger.debug("Got MCP streams")
            self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            logger.debug("Initializing session")
            await asyncio.wait_for(self.session.initialize(), timeout=5.0)
            logger.debug("Session initialized")
            
            logger.debug("Listing tools")
            tools = await asyncio.wait_for(self.session.list_tools(), timeout=5.0)
            logger.debug("Got tools list")
            
            return tools, self.session
                
        except asyncio.TimeoutError:
            logger.error("Operation timed out")
            raise
        except Exception as e:
            logger.error(f"Error during connection: {e}")
            raise
        finally:
            # Cancel stderr reader and cleanup
            stderr_task.cancel()
            try:
                await stderr_task
            except asyncio.CancelledError:
                pass
            
            if process.returncode is None:
                process.terminate()
                await process.wait()
        
    async def connect(self, command: str, args: list[str]):
        """Connect to an MCP server"""
        logger.debug(f"Connecting to MCP server: {command}")
        server_params = StdioServerParameters(command=command, args=args)
        
        # Use AsyncExitStack to manage the async contexts
        stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
        read, write = stdio_transport
        
        logger.debug("Got MCP streams")
        self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
        
        logger.debug("Initializing session")
        await self.session.initialize()
        logger.debug("Session initialized")
        
        tools = await self.session.list_tools()
        logger.debug(f"Got tools: {tools}")
        return tools
        
    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Call a tool on the MCP server"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
        return await self.session.call_tool(tool_name, arguments)
        
    async def cleanup(self):
        """Clean up resources"""
        await self.exit_stack.aclose()
        
        if self.loop and not self.loop.is_closed():
            logger.debug("Closing event loop")
            self.loop.close()