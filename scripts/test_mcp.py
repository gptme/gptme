#!/usr/bin/env python
import asyncio
import logging
import sys

from gptme.mcp import MCPClient

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

async def main():
    client = MCPClient()
    
    try:
        # Connect to server
        tools = await client.connect(
            command="docker",
            args=[
                "run",
                "--rm",
                "-i",
                "mcp/sqlite",
                "--db-path",
                ":memory:"
            ]
        )
        
        logger.info(f"Connected! Available tools: {tools}")
        
        # Test a simple query
        result = await client.call_tool("list_tables", {})
        logger.info(f"Tables: {result}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise
    finally:
        await client.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(1) 