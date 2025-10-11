"""Tests for MCP discovery and management functionality."""

import pytest
from unittest.mock import patch

from gptme.mcp.registry import (
    MCPRegistry,
    MCPServerInfo,
    format_server_details,
    format_server_list,
)
from gptme.tools.mcp import execute_mcp


def test_mcp_server_info():
    """Test MCPServerInfo creation and conversion."""
    server = MCPServerInfo(
        name="test-server",
        description="A test server",
        command="test-command",
        args=["arg1", "arg2"],
        registry="official",
        tags=["tag1", "tag2"],
    )

    assert server.name == "test-server"
    assert server.description == "A test server"
    assert server.registry == "official"

    server_dict = server.to_dict()
    assert server_dict["name"] == "test-server"
    assert server_dict["tags"] == ["tag1", "tag2"]


def test_format_server_list():
    """Test formatting a list of servers."""
    servers = [
        MCPServerInfo(
            name="server1",
            description="First server",
            registry="official",
            tags=["tag1"],
        ),
        MCPServerInfo(
            name="server2",
            description="Second server",
            registry="mcp.so",
            tags=["tag2"],
        ),
    ]

    result = format_server_list(servers)
    assert "server1" in result
    assert "server2" in result
    assert "First server" in result
    assert "Second server" in result
    assert "official" in result
    assert "mcp.so" in result


def test_format_server_list_empty():
    """Test formatting an empty list of servers."""
    result = format_server_list([])
    assert result == "No servers found."


def test_format_server_details():
    """Test formatting detailed server information."""
    server = MCPServerInfo(
        name="test-server",
        description="A test server",
        command="uvx",
        args=["test-command"],
        registry="official",
        tags=["test", "example"],
        author="Test Author",
        version="1.0.0",
        repository="https://github.com/test/test-server",
        install_command="uvx install test-server",
    )

    result = format_server_details(server)
    assert "test-server" in result
    assert "A test server" in result
    assert "Test Author" in result
    assert "1.0.0" in result
    assert "uvx install test-server" in result
    assert "[[mcp.servers]]" in result


@pytest.mark.slow
def test_mcp_registry_search_all():
    """Test searching all registries (may fail if registries are down)."""
    registry = MCPRegistry()

    # This is a real API call, so we expect it might fail in CI
    # We'll use a try-except to make the test more robust
    try:
        results = registry.search_all("", limit=5)
        # If it succeeds, verify the structure
        assert isinstance(results, list)
        for server in results:
            assert isinstance(server, MCPServerInfo)
            assert server.name
            assert server.registry in ["official", "mcp.so"]
    except Exception as e:
        pytest.skip(f"Registry search failed (expected in CI): {e}")


def test_execute_mcp_list():
    """Test the MCP list command."""
    from gptme.config import Config, MCPConfig, MCPServerConfig

    # Create a mock config with some servers
    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(
                name="test-server",
                enabled=True,
                command="test-command",
            ),
        ],
    )

    with patch("gptme.tools.mcp_adapter.get_config", return_value=config):
        # Execute list command
        def confirm(x):
            return True

        messages = list(execute_mcp("list", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "test-server" in messages[0].content


def test_execute_mcp_search():
    """Test the MCP search command."""
    # Mock the search function
    mock_servers = [
        MCPServerInfo(
            name="sqlite",
            description="SQLite MCP server",
            registry="official",
        ),
    ]

    with patch(
        "gptme.tools.mcp.search_mcp_servers",
        return_value=format_server_list(mock_servers),
    ):

        def confirm(x):
            return True

        messages = list(execute_mcp("search database", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_info():
    """Test the MCP info command."""
    # Mock the get_server_details function
    mock_server = MCPServerInfo(
        name="sqlite",
        description="SQLite MCP server",
        registry="official",
        command="uvx",
        args=["mcp-server-sqlite"],
    )

    with patch(
        "gptme.tools.mcp.get_mcp_server_info",
        return_value=format_server_details(mock_server),
    ):

        def confirm(x):
            return True

        messages = list(execute_mcp("info sqlite", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_unknown_command():
    """Test handling of unknown MCP commands."""

    def confirm(x):
        return True

    messages = list(execute_mcp("unknown-command", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Unknown MCP command" in messages[0].content


def test_execute_mcp_no_command():
    """Test handling when no command is provided."""

    def confirm(x):
        return True

    messages = list(execute_mcp(None, None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "No command provided" in messages[0].content
