import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
import tomlkit

# Import the minimal set of required modules
from gptme.config import UserConfig, MCPConfig, MCPServerConfig


@pytest.fixture
def test_config_path(tmp_path) -> Generator[Path, None, None]:
    """Create a temporary config file for testing"""
    # support both pipx and uvx
    pyx_cmd, pyx_args = (
        ("uvx", ["--from"]) if shutil.which("uvx") else ("pipx", ["run", "--spec"])
    )
    if not shutil.which(pyx_cmd):
        pytest.skip("pipx or uvx not found in PATH")
    if not shutil.which("npx"):
        pytest.skip("npx not found in PATH")

    mcp_server_sqlite = {
        "name": "sqlite",
        "enabled": True,
        "command": pyx_cmd,
        "args": [
            *pyx_args,
            "git+ssh://git@github.com/modelcontextprotocol/servers#subdirectory=src/sqlite",
            "mcp-server-sqlite",
        ],
        "env": {},
    }

    mcp_server_memory = {
        "name": "memory",
        "enabled": True,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "env": {"MEMORY_FILE_PATH": str(tmp_path / "memory.json")},
    }

    config_data = {
        "prompt": {},
        "env": {},
        "mcp": {
            "enabled": True,
            "auto_start": True,
            "servers": [mcp_server_sqlite, mcp_server_memory],
        },
    }

    config_file = tmp_path / "config.toml"
    with open(config_file, "w") as f:
        tomlkit.dump(config_data, f)

    os.environ["GPTME_CONFIG"] = str(config_file)
    yield config_file
    del os.environ["GPTME_CONFIG"]


@pytest.fixture
def mcp_config(test_config_path) -> UserConfig:
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

    return UserConfig(prompt={}, env={}, mcp=mcp)


@pytest.fixture
def mcp_client(mcp_config):
    """Create an MCP client instance"""
    from gptme.mcp import MCPClient

    return MCPClient(config=mcp_config)


@pytest.mark.xfail(reason="Timeout in CI", strict=False)
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


@pytest.mark.xfail(reason="Timeout in CI", strict=False)
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


@pytest.mark.xfail(reason="Timeout in CI", strict=False)
@pytest.mark.slow
def test_memory_connection(mcp_client):
    """Test connecting to Memory MCP server"""
    tools, session = mcp_client.connect("memory")
    assert tools is not None
    assert session is not None

    # Verify memory tools are available
    tool_names = [t.name for t in tools.tools]
    assert "create_entities" in tool_names
    assert "create_relations" in tool_names
    assert "add_observations" in tool_names
    assert "read_graph" in tool_names
    assert "search_nodes" in tool_names


@pytest.mark.xfail(reason="Timeout in CI", strict=False)
@pytest.mark.slow
def test_memory_operations(mcp_client):
    """Test Memory operations in sequence"""
    mcp_client.connect("memory")

    # Create test entity
    create_result = mcp_client.call_tool(
        "create_entities",
        {
            "entities": [
                {
                    "name": "test_user",
                    "entityType": "person",
                    "observations": ["Likes programming", "Uses Python"],
                }
            ]
        },
    )
    assert create_result is not None

    # Add observation
    add_result = mcp_client.call_tool(
        "add_observations",
        {
            "observations": [
                {"entityName": "test_user", "contents": ["Contributes to open source"]}
            ]
        },
    )
    assert add_result is not None

    # Read graph
    read_result = mcp_client.call_tool("read_graph", {})
    assert "test_user" in str(read_result)
    assert "Likes programming" in str(read_result)
    assert "Contributes to open source" in str(read_result)

    # Search nodes
    search_result = mcp_client.call_tool("search_nodes", {"query": "Python"})
    assert "test_user" in str(search_result)
