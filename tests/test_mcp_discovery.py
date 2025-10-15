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


def test_execute_mcp_search_with_json_args():
    """Test MCP search command with JSON arguments."""
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

        # Test with valid JSON for registry and limit
        code = 'search database\n{"registry": "official", "limit": "5"}'
        messages = list(execute_mcp(code, None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_search_with_invalid_json():
    """Test MCP search command with invalid JSON arguments (should fallback gracefully)."""
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

        # Test with invalid JSON - should use defaults
        code = "search database\n{invalid json"
        messages = list(execute_mcp(code, None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_info_no_server_name():
    """Test info command without server name."""

    def confirm(x):
        return True

    messages = list(execute_mcp("info", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: info <server-name>" in messages[0].content


def test_execute_mcp_info_local_http_server():
    """Test info command with locally configured HTTP server."""
    from gptme.config import Config, MCPConfig, MCPServerConfig

    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(
                name="test-http-server",
                enabled=True,
                url="http://localhost:8080/mcp",
                headers={"Authorization": "Bearer token"},
            ),
        ],
    )

    with patch("gptme.config.get_config", return_value=config):

        def confirm(x):
            return True

        messages = list(execute_mcp("info test-http-server", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        assert "test-http-server" in content
        assert "configured locally" in content
        assert "HTTP" in content
        assert "http://localhost:8080/mcp" in content
        assert "Headers" in content


def test_execute_mcp_info_local_stdio_server():
    """Test info command with locally configured stdio server."""
    from gptme.config import Config, MCPConfig, MCPServerConfig

    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(
                name="test-stdio-server",
                enabled=False,
                command="test-command",
                args=["arg1", "arg2"],
            ),
        ],
    )

    with patch("gptme.config.get_config", return_value=config):

        def confirm(x):
            return True

        messages = list(execute_mcp("info test-stdio-server", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        assert "test-stdio-server" in content
        assert "configured locally" in content
        assert "stdio" in content
        assert "test-command" in content
        assert "arg1, arg2" in content
        assert "No" in content  # Enabled: No


def test_execute_mcp_info_not_found_locally():
    """Test info command for server not configured locally."""
    from gptme.config import Config, MCPConfig

    config = Config()
    config.user.mcp = MCPConfig(enabled=True, servers=[])

    mock_server = MCPServerInfo(
        name="remote-server",
        description="A remote server",
        registry="official",
    )

    with (
        patch("gptme.config.get_config", return_value=config),
        patch(
            "gptme.tools.mcp.get_mcp_server_info",
            return_value=format_server_details(mock_server),
        ),
    ):

        def confirm(x):
            return True

        messages = list(execute_mcp("info remote-server", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        # Server was found in registry, so no "not configured locally" prefix
        assert "remote-server" in content
        assert "A remote server" in content
        assert "official" in content


def test_execute_mcp_load_no_server_name():
    """Test load command without server name."""

    def confirm(x):
        return True

    messages = list(execute_mcp("load", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: load <server-name>" in messages[0].content


def test_execute_mcp_load_cancelled():
    """Test load command when user cancels confirmation."""

    def confirm(x):
        return False  # User cancels

    messages = list(execute_mcp("load test-server", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Cancelled" in messages[0].content


def test_execute_mcp_load_with_config_override():
    """Test load command with config override (JSON args)."""
    with patch("gptme.tools.mcp.load_mcp_server", return_value="Server loaded"):

        def confirm(x):
            return True

        code = 'load test-server\n{"enabled": true}'
        messages = list(execute_mcp(code, None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Server loaded" in messages[0].content


def test_execute_mcp_unload_no_server_name():
    """Test unload command without server name."""

    def confirm(x):
        return True

    messages = list(execute_mcp("unload", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: unload <server-name>" in messages[0].content


def test_execute_mcp_unload_cancelled():
    """Test unload command when user cancels confirmation."""

    def confirm(x):
        return False  # User cancels

    messages = list(execute_mcp("unload test-server", None, None, confirm))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Cancelled" in messages[0].content


def test_execute_mcp_exception_handling():
    """Test exception handling in execute_mcp."""
    with patch(
        "gptme.tools.mcp.search_mcp_servers",
        side_effect=RuntimeError("Test error"),
    ):

        def confirm(x):
            return True

        messages = list(execute_mcp("search test", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Error" in messages[0].content
        assert "Test error" in messages[0].content


def test_execute_mcp_info_server_not_in_registry():
    """Test info command when server is not found in registry."""
    from gptme.config import Config, MCPConfig

    config = Config()
    config.user.mcp = MCPConfig(enabled=True, servers=[])

    # Mock get_mcp_server_info to return "not found" message
    not_found_message = "Server 'unknown-server' not found in registries."

    with (
        patch("gptme.config.get_config", return_value=config),
        patch(
            "gptme.tools.mcp.get_mcp_server_info",
            return_value=not_found_message,
        ),
    ):

        def confirm(x):
            return True

        messages = list(execute_mcp("info unknown-server", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        # Should have both "not configured locally" prefix and "not found" message
        assert "not configured locally" in content
        assert "not found in registries" in content


def test_execute_mcp_unload_success():
    """Test successful unload command."""
    with patch("gptme.tools.mcp.unload_mcp_server", return_value="Server unloaded"):

        def confirm(x):
            return True

        messages = list(execute_mcp("unload test-server", None, None, confirm))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Server unloaded" in messages[0].content
