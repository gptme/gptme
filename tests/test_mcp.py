import os
import logging
import pytest
import tomlkit
from pathlib import Path
from collections.abc import Generator

# Import the minimal set of required modules
from gptme.config import Config, MCPConfig, MCPServerConfig

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@pytest.fixture
def test_config_path(tmp_path) -> Generator[Path, None, None]:
    """Create a temporary config file for testing"""
    config_data = {
        "prompt": {},
        "env": {},
        "mcp": {
            "enabled": True,
            "auto_start": True,
            "servers": [
                {
                    "name": "sqlite",
                    "enabled": True,
                    "command": "uvx",
                    "args": [
                        "--from",
                        "git+ssh://git@github.com/modelcontextprotocol/servers#subdirectory=src/sqlite",
                        "mcp-server-sqlite",
                    ],
                    "env": {},
                },
                {
                    "name": "weatherAPI",
                    "enabled": True,
                    "command": "uvx",
                    "args": [
                        "--from",
                        "git+https://github.com/adhikasp/mcp-weather.git",
                        "mcp-weather",
                    ],
                    "env": {"WEATHER_API_KEY": os.environ.get("WEATHER_API_KEY", "")},
                },
            ],
        },
    }

    config_file = tmp_path / "config.toml"
    with open(config_file, "w") as f:
        tomlkit.dump(config_data, f)

    os.environ["GPTME_CONFIG"] = str(config_file)
    yield config_file
    del os.environ["GPTME_CONFIG"]


@pytest.fixture
def mcp_config(test_config_path) -> Config:
    """Load MCP config from the test config file"""
    with open(test_config_path) as f:
        config_data = tomlkit.load(f)

    mcp_data = config_data.get("mcp", {})
    servers = [MCPServerConfig(**s) for s in mcp_data.get("servers", [])]
    mcp = MCPConfig(
        enabled=mcp_data.get("enabled", False),
        auto_start=mcp_data.get("auto_start", False),
        servers=servers,
    )

    return Config(prompt={}, env={}, mcp=mcp)


@pytest.fixture
def mcp_client(mcp_config):
    """Create an MCP client instance"""
    from gptme.mcp import MCPClient

    return MCPClient(config=mcp_config)


@pytest.mark.slow
def test_sqlite_connection(mcp_client):
    """Test connecting to SQLite MCP server"""
    tools, session = mcp_client.connect("sqlite")
    assert tools is not None
    assert session is not None

    # Verify tools are available
    tool_names = [t.name for t in tools.tools]
    assert "create_table" in tool_names
    assert "write_query" in tool_names
    assert "read_query" in tool_names


@pytest.mark.slow
def test_sqlite_operations(mcp_client):
    """Test SQLite operations in sequence"""
    mcp_client.connect("sqlite")

    # Create test table
    create_result = mcp_client.call_tool(
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
    assert create_result is not None

    # Insert test data
    insert_result = mcp_client.call_tool(
        "write_query",
        {
            "query": "INSERT INTO test_users (username, email) VALUES ('test1', 'test1@example.com')"
        },
    )
    assert insert_result is not None

    # Read test data
    read_result = mcp_client.call_tool(
        "read_query",
        {"query": "SELECT * FROM test_users"},
    )
    assert "test1" in read_result
    assert "test1@example.com" in read_result


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("WEATHER_API_KEY"), reason="WEATHER_API_KEY not set"
)
def test_weather_connection(mcp_client):
    """Test connecting to Weather MCP server"""
    tools, session = mcp_client.connect("weatherAPI")
    assert tools is not None
    assert session is not None

    # Verify weather tools are available
    tool_names = [t.name for t in tools.tools]
    assert "get_hourly_weather" in tool_names


@pytest.mark.slow
@pytest.mark.skipif(
    not os.environ.get("WEATHER_API_KEY"), reason="WEATHER_API_KEY not set"
)
def test_weather_query(mcp_client):
    """Test getting weather data"""
    mcp_client.connect("weatherAPI")

    # Get weather for Stockholm
    result = mcp_client.call_tool(
        "get_hourly_weather", {"location": "Stockholm, Sweden"}
    )
    assert result is not None
    # Basic validation that we got weather data
    assert any(
        term in result.lower() for term in ["temperature", "weather", "forecast"]
    )
