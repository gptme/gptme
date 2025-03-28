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
        # Connect to weather API server
        tools = client.connect("weatherAPI")

        logger.info(f"Connected! Available tools: {tools}")
        for tool in tools[0].tools:
            logger.info(f"- {tool.name}: {tool.description}")

        # Test getting weather for a location
        logger.info("\nTesting weather for Stockholm")
        result = client.call_tool(
            "get_hourly_weather", {"location": "Stockholm, Sweden"}
        )
        logger.info(f"Weather result: {result}")

    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise


if __name__ == "__main__":
    main()
