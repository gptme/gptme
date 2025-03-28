#!/usr/bin/env python3
import os
import logging
import tomlkit

from gptme.mcp import MCPClient
from gptme.config import Config, MCPConfig, MCPServerConfig

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_test_config() -> Config:
    """Load config from GPTME_CONFIG environment variable"""
    config_path = os.environ.get("GPTME_CONFIG")
    if not config_path:
        raise ValueError("GPTME_CONFIG environment variable not set")

    with open(config_path) as f:
        config_data = tomlkit.load(f)

    # Create MCP config from the data
    mcp_data = config_data.get("mcp", {})
    servers = [MCPServerConfig(**s) for s in mcp_data.get("servers", [])]
    mcp = MCPConfig(
        enabled=mcp_data.get("enabled", False),
        auto_start=mcp_data.get("auto_start", False),
        servers=servers,
    )

    # Create main config (with empty prompt and env since we only need MCP)
    return Config(prompt={}, env={}, mcp=mcp)


def main():
    # Load config from environment
    config = load_test_config()
    client = MCPClient(config=config)

    try:
        # Connect to server using name from config
        tools = client.connect("sqlite")

        logger.info(f"Connected! Available tools: {tools}")
        for tool in tools[0].tools:
            logger.info(f"- {tool.name}: {tool.description}")

        # Create a test table
        create_result = client.call_tool(
            "create_table",
            {
                "query": """
                CREATE TABLE IF NOT EXISTS test_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    email TEXT NOT NULL
                )
                """
            },
        )
        logger.info(f"Create table result: {create_result}")

        # Try multiple operations in sequence to verify persistence
        operations = [
            ("list_tables", {}, "Tables after creation"),
            (
                "write_query",
                {
                    "query": "INSERT INTO test_users (username, email) VALUES ('test1', 'test1@example.com')"
                },
                "First insert",
            ),
            (
                "write_query",
                {
                    "query": "INSERT INTO test_users (username, email) VALUES ('test2', 'test2@example.com')"
                },
                "Second insert",
            ),
            ("read_query", {"query": "SELECT * FROM test_users"}, "Select all users"),
        ]

        # Execute operations in sequence
        for tool_name, params, description in operations:
            result = client.call_tool(tool_name, params)
            logger.info(f"{description}: {result}")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise


if __name__ == "__main__":
    main()
