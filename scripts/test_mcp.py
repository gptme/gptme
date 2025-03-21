#!/usr/bin/env python
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

def main():
    client = MCPClient()
    
    try:
        # Connect to server
        tools = client.connect(
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
        
        logger.info("Connected! Available tools:")
        for tool in tools.tools:
            logger.info(f"- {tool.name}: {tool.description}")
        
        # List tables
        tables = client.call_tool("list_tables", {})
        logger.info(f"Tables: {tables}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise
    finally:
        client.cleanup()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(1) 