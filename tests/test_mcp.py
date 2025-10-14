import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
import tomlkit

# Import the minimal set of required modules
from gptme.config import MCPConfig, MCPServerConfig, UserConfig


def test_mcp_cli_commands():
    """Test MCP CLI command logic"""
    from click.testing import CliRunner
    from gptme.util.cli import mcp_info

    # Test with mock data - this would normally use the config system
    runner = CliRunner()

    # Test info command with non-existent server
    result = runner.invoke(mcp_info, ["nonexistent"])
    # Updated to match improved error message that searches registries
    assert "not configured locally" in result.output
    assert "not found in registries either" in result.output


def test_mcp_server_config_http():
    """Test HTTP MCP server configuration"""
    # Test HTTP server
    http_server = MCPServerConfig(
        name="test-http",
        url="https://example.com/mcp",
        headers={"Authorization": "Bearer token"},
    )
    assert http_server.is_http is True
    assert http_server.url == "https://example.com/mcp"
    assert http_server.headers["Authorization"] == "Bearer token"

    # Test stdio server
    stdio_server = MCPServerConfig(name="test-stdio", command="echo", args=["hello"])
    assert stdio_server.is_http is False
    assert stdio_server.command == "echo"


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

    return UserConfig(mcp=mcp)


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


# Comprehensive tests for execute_mcp tool function
def test_execute_mcp_no_command():
    """Test execute_mcp with no command provided"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp(None, None, None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "No command provided" in messages[0].content


def test_execute_mcp_empty_command():
    """Test execute_mcp with empty command"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("", None, None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "No command provided" in messages[0].content


def test_execute_mcp_unknown_command():
    """Test execute_mcp with unknown command"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("unknown_cmd", None, None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Unknown MCP command: unknown_cmd" in messages[0].content
    assert "Available commands:" in messages[0].content


def test_execute_mcp_search_basic():
    """Test execute_mcp search command"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.search_mcp_servers") as mock_search:
        mock_search.return_value = "Found 3 servers"
        messages = list(execute_mcp("search test", None, None, lambda _: True))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Found 3 servers" in messages[0].content
        mock_search.assert_called_once_with("test", "all", 10)


def test_execute_mcp_search_with_json_args():
    """Test execute_mcp search with JSON arguments"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    code = 'search query\n{"registry": "official", "limit": "5"}'
    with patch("gptme.tools.mcp.search_mcp_servers") as mock_search:
        mock_search.return_value = "Found 2 servers"
        messages = list(execute_mcp(code, None, None, lambda _: True))

        assert len(messages) == 1
        mock_search.assert_called_once_with("query", "official", 5)


def test_execute_mcp_search_no_query():
    """Test execute_mcp search without query"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.search_mcp_servers") as mock_search:
        mock_search.return_value = "All servers"
        messages = list(execute_mcp("search", None, None, lambda _: True))

        assert len(messages) == 1
        mock_search.assert_called_once_with("", "all", 10)


def test_execute_mcp_info_no_server_name():
    """Test execute_mcp info without server name"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("info", None, None, lambda _: True))
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: info <server-name>" in messages[0].content


def test_execute_mcp_info_local_server():
    """Test execute_mcp info with locally configured server"""
    from gptme.tools.mcp import execute_mcp
    from gptme.config import MCPServerConfig
    from unittest.mock import patch, MagicMock

    # Mock a local server configuration
    mock_server = MCPServerConfig(
        name="test-server", command="python", args=["-m", "test"], enabled=True
    )

    mock_config = MagicMock()
    mock_config.mcp.servers = [mock_server]

    with patch("gptme.config.get_config", return_value=mock_config):
        messages = list(execute_mcp("info test-server", None, None, lambda _: True))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "test-server (configured locally)" in messages[0].content
        assert "stdio" in messages[0].content
        assert "**Enabled:** Yes" in messages[0].content


def test_execute_mcp_info_http_server():
    """Test execute_mcp info with HTTP server"""
    from gptme.tools.mcp import execute_mcp
    from gptme.config import MCPServerConfig
    from unittest.mock import patch, MagicMock

    # Mock HTTP server
    mock_server = MCPServerConfig(
        name="http-server",
        url="http://localhost:8080",
        headers={"Authorization": "Bearer token"},
        enabled=False,
    )

    mock_config = MagicMock()
    mock_config.mcp.servers = [mock_server]

    with patch("gptme.config.get_config", return_value=mock_config):
        messages = list(execute_mcp("info http-server", None, None, lambda _: True))

        assert len(messages) == 1
        content = messages[0].content
        assert "http-server (configured locally)" in content
        assert "HTTP" in content
        assert "**Enabled:** No" in content
        assert "http://localhost:8080" in content
        assert "**Headers:** 1 configured" in content


def test_execute_mcp_info_registry_server():
    """Test execute_mcp info for server not in local config"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch, MagicMock

    mock_config = MagicMock()
    mock_config.mcp.servers = []

    with (
        patch("gptme.config.get_config", return_value=mock_config),
        patch("gptme.tools.mcp.get_mcp_server_info") as mock_info,
    ):
        mock_info.return_value = "Server details from registry"
        messages = list(execute_mcp("info registry-server", None, None, lambda _: True))

        assert len(messages) == 1
        content = messages[0].content
        assert "Server details from registry" in content

        mock_info.assert_called_once_with("registry-server")


def test_execute_mcp_info_not_found():
    """Test execute_mcp info for non-existent server"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch, MagicMock

    mock_config = MagicMock()
    mock_config.mcp.servers = []

    with (
        patch("gptme.config.get_config", return_value=mock_config),
        patch("gptme.tools.mcp.get_mcp_server_info") as mock_info,
    ):
        mock_info.return_value = "Server not found"
        messages = list(execute_mcp("info missing-server", None, None, lambda _: True))

        assert len(messages) == 1
        content = messages[0].content
        assert "not configured locally" in content
        assert "not found" in content.lower()


def test_execute_mcp_load_no_server_name():
    """Test execute_mcp load without server name"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("load", None, None, lambda _: True))
    assert len(messages) == 1
    assert "Usage: load <server-name>" in messages[0].content


def test_execute_mcp_load_cancelled():
    """Test execute_mcp load when user cancels"""
    from gptme.tools.mcp import execute_mcp

    # Confirmation function returns False (user cancels)
    messages = list(execute_mcp("load test-server", None, None, lambda _: False))
    assert len(messages) == 1
    assert "Cancelled" in messages[0].content


def test_execute_mcp_load_success():
    """Test execute_mcp load command"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.load_mcp_server") as mock_load:
        mock_load.return_value = "Server loaded successfully"
        messages = list(execute_mcp("load test-server", None, None, lambda _: True))

        assert len(messages) == 1
        assert "Server loaded successfully" in messages[0].content
        mock_load.assert_called_once_with("test-server", None)


def test_execute_mcp_load_with_config():
    """Test execute_mcp load with config override"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    code = 'load test-server\n{"url": "http://custom:8080"}'
    with patch("gptme.tools.mcp.load_mcp_server") as mock_load:
        mock_load.return_value = "Server loaded with custom config"
        messages = list(execute_mcp(code, None, None, lambda _: True))

        assert len(messages) == 1
        assert "Server loaded with custom config" in messages[0].content
        # Verify config override was passed
        call_args = mock_load.call_args
        assert call_args[0][0] == "test-server"
        assert call_args[0][1] == {"url": "http://custom:8080"}


def test_execute_mcp_unload_no_server_name():
    """Test execute_mcp unload without server name"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("unload", None, None, lambda _: True))
    assert len(messages) == 1
    assert "Usage: unload <server-name>" in messages[0].content


def test_execute_mcp_unload_cancelled():
    """Test execute_mcp unload when user cancels"""
    from gptme.tools.mcp import execute_mcp

    messages = list(execute_mcp("unload test-server", None, None, lambda _: False))
    assert len(messages) == 1
    assert "Cancelled" in messages[0].content


def test_execute_mcp_unload_success():
    """Test execute_mcp unload command"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.unload_mcp_server") as mock_unload:
        mock_unload.return_value = "Server unloaded successfully"
        messages = list(execute_mcp("unload test-server", None, None, lambda _: True))

        assert len(messages) == 1
        assert "Server unloaded successfully" in messages[0].content
        mock_unload.assert_called_once_with("test-server")


def test_execute_mcp_list():
    """Test execute_mcp list command"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.list_loaded_servers") as mock_list:
        mock_list.return_value = "2 servers loaded:\n- server1\n- server2"
        messages = list(execute_mcp("list", None, None, lambda _: True))

        assert len(messages) == 1
        assert "2 servers loaded" in messages[0].content
        mock_list.assert_called_once()


def test_execute_mcp_exception_handling():
    """Test execute_mcp exception handling"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    with patch("gptme.tools.mcp.search_mcp_servers") as mock_search:
        mock_search.side_effect = Exception("Test error")
        messages = list(execute_mcp("search test", None, None, lambda _: True))

        assert len(messages) == 1
        assert "Error: Test error" in messages[0].content


def test_execute_mcp_invalid_json_args():
    """Test execute_mcp with invalid JSON in args"""
    from gptme.tools.mcp import execute_mcp
    from unittest.mock import patch

    # Invalid JSON should be ignored, using defaults
    code = "search query\n{invalid json}"
    with patch("gptme.tools.mcp.search_mcp_servers") as mock_search:
        mock_search.return_value = "Results"
        messages = list(execute_mcp(code, None, None, lambda _: True))

        assert len(messages) == 1
        # Should use defaults since JSON parsing failed
        mock_search.assert_called_once_with("query", "all", 10)
