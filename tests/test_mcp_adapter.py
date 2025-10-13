"""Tests for MCP adapter functionality."""

import pytest
from unittest.mock import MagicMock, patch

from gptme.config import Config, MCPConfig, MCPServerConfig
from gptme.tools.mcp_adapter import (
    create_mcp_tools,
    create_mcp_execute_function,
    search_mcp_servers,
    get_mcp_server_info,
    load_mcp_server,
    unload_mcp_server,
    list_loaded_servers,
    _dynamic_servers,
)
from gptme.mcp.registry import MCPServerInfo


@pytest.fixture
def mock_config():
    """Create a mock config with MCP enabled."""
    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(
                name="test-server",
                enabled=True,
                command="test-command",
                args=["arg1"],
            ),
        ],
    )
    return config


@pytest.fixture
def mock_disabled_config():
    """Create a mock config with MCP disabled."""
    config = Config()
    config.user.mcp = MCPConfig(enabled=False, servers=[])
    return config


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCP client."""
    client = MagicMock()

    # Mock tool definition
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.description = "A test tool"
    mock_tool.inputSchema = {
        "type": "object",
        "properties": {
            "param1": {"type": "string", "description": "First parameter"},
            "param2": {"type": "number", "description": "Second parameter"},
        },
        "required": ["param1"],
    }

    # Mock tools object
    mock_tools = MagicMock()
    mock_tools.tools = [mock_tool]

    # Mock session
    mock_session = MagicMock()

    client.connect.return_value = (mock_tools, mock_session)
    return client


def test_create_mcp_tools_disabled(mock_disabled_config):
    """Test create_mcp_tools when MCP is disabled."""
    tools = create_mcp_tools(mock_disabled_config)
    assert tools == []


def test_create_mcp_tools_enabled(mock_config, mock_mcp_client):
    """Test create_mcp_tools when MCP is enabled."""
    with patch("gptme.tools.mcp_adapter.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)

        assert len(tools) > 0
        assert any("test-server.test_tool" in tool.name for tool in tools)


def test_create_mcp_tools_connection_error(mock_config):
    """Test create_mcp_tools when server connection fails."""
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("Connection failed")

    with patch("gptme.tools.mcp_adapter.MCPClient", return_value=mock_client):
        # Should not raise, just skip the failed server
        tools = create_mcp_tools(mock_config)
        # May be empty if connection failed
        assert isinstance(tools, list)


def test_create_mcp_execute_function():
    """Test create_mcp_execute_function creates valid execute function."""
    mock_client = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "test_tool"
    mock_tool.inputSchema = {
        "properties": {"param1": {"type": "string", "description": "Test param"}},
        "required": ["param1"],
    }

    # Mock the call_tool method to return a result
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text="Success")]
    mock_result.isError = False
    mock_client.call_tool.return_value = mock_result

    execute_fn = create_mcp_execute_function("server.test_tool", mock_client)

    # Test that execute function is callable
    assert callable(execute_fn)

    # Test execution with valid JSON
    def confirm(x: str) -> bool:
        return True

    result = execute_fn('{"param1": "value"}', None, None, confirm)
    # Handle both generator and list returns
    messages = list(result) if hasattr(result, "__iter__") else [result]
    assert len(messages) > 0


def test_search_mcp_servers_all():
    """Test search_mcp_servers with 'all' registry."""
    mock_servers = [
        MCPServerInfo(name="server1", description="First", registry="official"),
        MCPServerInfo(name="server2", description="Second", registry="mcp.so"),
    ]

    with patch(
        "gptme.tools.mcp_adapter._registry.search_all", return_value=mock_servers
    ):
        result = search_mcp_servers("test", "all", 10)
        assert "server1" in result
        assert "server2" in result


def test_search_mcp_servers_official():
    """Test search_mcp_servers with 'official' registry."""
    mock_servers = [
        MCPServerInfo(
            name="official-server", description="Official", registry="official"
        ),
    ]

    with patch(
        "gptme.tools.mcp_adapter._registry.search_official_registry",
        return_value=mock_servers,
    ):
        result = search_mcp_servers("test", "official", 10)
        assert "official-server" in result


def test_search_mcp_servers_mcp_so():
    """Test search_mcp_servers with 'mcp.so' registry."""
    mock_servers = [
        MCPServerInfo(name="mcpso-server", description="MCP.so", registry="mcp.so"),
    ]

    with patch(
        "gptme.tools.mcp_adapter._registry.search_mcp_so", return_value=mock_servers
    ):
        result = search_mcp_servers("test", "mcp.so", 10)
        assert "mcpso-server" in result


def test_search_mcp_servers_unknown_registry():
    """Test search_mcp_servers with unknown registry."""
    result = search_mcp_servers("test", "unknown", 10)
    assert "Unknown registry" in result


def test_get_mcp_server_info_found():
    """Test get_mcp_server_info when server is found."""
    mock_server = MCPServerInfo(
        name="test-server",
        description="Test server",
        registry="official",
    )

    with patch(
        "gptme.tools.mcp_adapter._registry.get_server_details", return_value=mock_server
    ):
        result = get_mcp_server_info("test-server")
        assert "test-server" in result
        assert "Test server" in result


def test_get_mcp_server_info_not_found():
    """Test get_mcp_server_info when server is not found."""
    with patch(
        "gptme.tools.mcp_adapter._registry.get_server_details", return_value=None
    ):
        result = get_mcp_server_info("nonexistent")
        assert "not found" in result


def test_load_mcp_server_already_loaded():
    """Test load_mcp_server when server is already loaded."""
    # Add server to dynamic servers cache
    _dynamic_servers["test-server"] = MagicMock()

    result = load_mcp_server("test-server")
    assert "already loaded" in result

    # Cleanup
    del _dynamic_servers["test-server"]


def test_load_mcp_server_in_config(mock_config):
    """Test load_mcp_server when server is in config."""
    mock_client = MagicMock()
    mock_tools = MagicMock()
    mock_tools.tools = []
    mock_session = MagicMock()
    mock_client.connect.return_value = (mock_tools, mock_session)

    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
        patch("gptme.tools.mcp_adapter.MCPClient", return_value=mock_client),
    ):
        result = load_mcp_server("test-server")
        assert "Successfully loaded" in result or "tools registered" in result

        # Cleanup
        if "test-server" in _dynamic_servers:
            del _dynamic_servers["test-server"]


def test_unload_mcp_server_not_loaded():
    """Test unload_mcp_server when server is not loaded."""
    result = unload_mcp_server("nonexistent")
    assert "not loaded" in result


def test_unload_mcp_server_success():
    """Test unload_mcp_server when server is loaded."""
    # Add a mock server
    mock_client = MagicMock()
    _dynamic_servers["test-server"] = mock_client

    result = unload_mcp_server("test-server")
    assert "Successfully unloaded" in result or "unloaded" in result
    assert "test-server" not in _dynamic_servers


def test_list_loaded_servers_empty():
    """Test list_loaded_servers when no servers are configured."""
    # Mock empty config
    empty_config = Config()
    empty_config.user.mcp = MCPConfig(enabled=True, servers=[])

    with patch("gptme.tools.mcp_adapter.get_config", return_value=empty_config):
        result = list_loaded_servers()
        assert "No MCP servers configured" in result


def test_list_loaded_servers_with_servers():
    """Test list_loaded_servers shows configured servers and marks dynamic ones."""
    # Create config with servers
    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(name="server1", enabled=True, command="cmd1"),
            MCPServerConfig(name="server2", enabled=False, command="cmd2"),
        ],
    )

    # Mark server1 as dynamically loaded
    _dynamic_servers["server1"] = MagicMock()

    with patch("gptme.tools.mcp_adapter.get_config", return_value=config):
        result = list_loaded_servers()
        assert "server1" in result
        assert "server2" in result
        assert "(dynamic)" in result  # server1 should be marked as dynamic

    # Cleanup
    _dynamic_servers.clear()
