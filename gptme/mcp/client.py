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
        
    def _run_async(self, coro):
        """Run an async operation in the event loop"""
        return self.loop.run_until_complete(coro)
        
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
        
    def connect(self, command: str, args: list[str]):
        """Synchronous connect method"""
        async def _connect():
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
            read, write = stdio_transport
            
            logger.debug("Got MCP streams")
            self.session = await self.exit_stack.enter_async_context(ClientSession(read, write))
            
            logger.debug("Initializing session")
            await self.session.initialize()
            logger.debug("Session initialized")
            
            return await self.session.list_tools()
            
        logger.debug(f"Connecting to MCP server: {command}")
        server_params = StdioServerParameters(command=command, args=args)
        return self._run_async(_connect())
        
    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """Synchronous tool call method"""
        if not self.session:
            raise RuntimeError("Not connected to MCP server")
            
        async def _call_tool():
            result = await self.session.call_tool(tool_name, arguments)
            if hasattr(result, 'content') and result.content:
                for content in result.content:
                    if content.type == 'text':
                        return content.text
            return str(result)
            
        return self._run_async(_call_tool())
    
    def cleanup(self):
        """Clean up resources and close connections."""
        try:
            # Create a new event loop for cleanup
            old_loop = self.loop
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            
            # Run cleanup in the new loop
            self.loop.run_until_complete(self.exit_stack.aclose())
        except Exception as e:
            logging.error(f"Error during cleanup: {e}")
        finally:
            self.loop.close()
            # Restore the old loop
            asyncio.set_event_loop(old_loop)