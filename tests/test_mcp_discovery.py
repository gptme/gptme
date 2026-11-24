"""Tests for MCP discovery and management functionality."""

from typing import Protocol, cast
from unittest.mock import MagicMock, patch

import pytest

from gptme.mcp.registry import (
    MCPRegistry,
    MCPServerInfo,
    format_server_details,
    format_server_list,
)
from gptme.tools.mcp import execute_mcp


class _CallAssertingMock(Protocol):
    """The mock assertion interface used by the search tests."""

    def assert_called_once_with(self, *args: object, **kwargs: object) -> None: ...


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


def test_mcp_registry_search_all_deduplication():
    """search_all must not return the same server twice when it appears in both registries."""
    registry = MCPRegistry()

    duplicate = MCPServerInfo(
        name="dup-server", description="Appears in both", registry="official"
    )
    official_results = [
        duplicate,
        MCPServerInfo(
            name="only-official", description="Only in official", registry="official"
        ),
    ]
    mcp_so_results = [
        MCPServerInfo(
            name="dup-server", description="Appears in both", registry="mcp.so"
        ),
        MCPServerInfo(
            name="only-mcp-so", description="Only in mcp.so", registry="mcp.so"
        ),
    ]

    with (
        patch.object(
            registry, "search_official_registry", return_value=official_results
        ),
        patch.object(registry, "search_mcp_so", return_value=mcp_so_results),
    ):
        results = registry.search_all("", limit=10)

    names = [r.name for r in results]
    assert names.count("dup-server") == 1, f"Duplicate found: {names}"
    assert len(results) == 3


def test_mcp_registry_search_all_respects_limit():
    """search_all must not return more than `limit` results."""
    registry = MCPRegistry()

    many = [
        MCPServerInfo(name=f"server-{i}", description="x", registry="official")
        for i in range(8)
    ]

    with (
        patch.object(registry, "search_official_registry", return_value=many[:5]),
        patch.object(registry, "search_mcp_so", return_value=many[5:]),
    ):
        results = registry.search_all("", limit=4)

    assert len(results) == 4


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

        messages = list(execute_mcp("list", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "test-server" in messages[0].content


@pytest.mark.parametrize(
    ("command", "query"),
    [
        ("search database", "database"),
        ("search browser automation", "browser automation"),
        ("search multiple query words", "multiple query words"),
        ("search   browser  automation", "browser  automation"),
        ("search\tbrowser\tautomation", "browser\tautomation"),
        ("search", ""),
        ("search   ", ""),
    ],
)
def test_execute_mcp_search(command: str, query: str) -> None:
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
    ) as mock_search:
        messages = list(execute_mcp(command, None, None))

        cast(_CallAssertingMock, mock_search).assert_called_once_with(query, "all", 10)
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
        messages = list(execute_mcp("info sqlite", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


@pytest.mark.parametrize(
    ("transport", "url"),
    [
        ("streamable-http", "https://mcp.example.net/sse?region=us&version=1"),
        ("sse", "https://mcp.example.net/events"),
    ],
)
def test_execute_mcp_info_from_official_registry(transport: str, url: str) -> None:
    """Show the first supported remote and its declared transport."""
    from gptme.config import Config

    name = "io.github.example/remote"
    with (
        patch("gptme.config.get_config", return_value=Config()),
        patch("gptme.mcp.registry.requests.get") as get,
    ):
        get.return_value.json.return_value = {
            "servers": [
                {
                    "server": {
                        "name": name,
                        "description": "A remote server",
                        "version": "1.0.0",
                        "remotes": [
                            {"type": "other", "url": "https://ignored.example.net/mcp"},
                            {"type": "streamable-http", "url": ""},
                            {"type": transport, "url": url},
                            {
                                "type": "streamable-http",
                                "url": "https://later.example.net/mcp",
                            },
                        ],
                    }
                }
            ]
        }

        (message,) = execute_mcp(f"info {name}", None, None)

    assert f'url = "{url}"' in message.content
    assert f'transport = "{transport}"' in message.content


@pytest.mark.parametrize("include_remote", [False, True])
def test_execute_mcp_info_for_official_registry_package(include_remote: bool) -> None:
    """Keep package configuration for package-only and hybrid servers."""
    from gptme.config import Config

    name = "io.github.example/packaged"
    server = {
        "name": name,
        "description": "A packaged server",
        "version": "1.0.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/mcp-server",
                "transport": {"type": "stdio"},
            }
        ],
    }
    if include_remote:
        server["remotes"] = [{"type": "sse", "url": "https://mcp.example.net/events"}]

    with (
        patch("gptme.config.get_config", return_value=Config()),
        patch("gptme.mcp.registry.requests.get") as get,
    ):
        get.return_value.json.return_value = {"servers": [{"server": server}]}
        (message,) = execute_mcp(f"info {name}", None, None)

    assert "npm install -g @example/mcp-server" in message.content
    assert 'command = "npx"\nargs = ["@example/mcp-server"]' in message.content
    assert "url =" not in message.content


@pytest.mark.parametrize(
    ("transport", "url", "use_sse"),
    [
        ("streamable-http", "https://mcp.example.net/sse?region=us&version=1", False),
        ("sse", "https://mcp.example.net/events", True),
    ],
)
def test_load_remote_from_official_registry(
    transport: str, url: str, use_sse: bool
) -> None:
    """Pass declared transports to the HTTP connection, not URL-path guesses."""
    from mcp.types import ListToolsResult

    from gptme.config import Config
    from gptme.mcp.client import MCPClient
    from gptme.tools.mcp_adapter import unload_mcp_server

    name = "io.github.example/remote"
    connection = (ListToolsResult(tools=[]), MagicMock())
    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=Config()),
        patch("gptme.tools.mcp.confirm", return_value=True),
        patch("gptme.mcp.registry.requests.get") as get,
        patch("gptme.mcp.client.build_oauth_provider", return_value=None),
        patch.object(
            MCPClient, "_setup_http_connection", return_value=connection
        ) as http,
        patch.object(
            MCPClient, "_setup_stdio_connection", return_value=connection
        ) as stdio,
    ):
        get.return_value.json.return_value = {
            "servers": [
                {
                    "server": {
                        "name": name,
                        "description": "A remote server",
                        "version": "1.0.0",
                        "remotes": [{"type": transport, "url": url}],
                    }
                }
            ]
        }
        (message,) = execute_mcp(f"load {name}", None, None)
        try:
            assert f"Successfully loaded server '{name}'" in message.content
            http.assert_awaited_once_with(url, {}, use_sse=use_sse, auth=None)
            stdio.assert_not_awaited()
        finally:
            unload_mcp_server(name)


def test_load_override_switches_saved_transport() -> None:
    """Apply a transport override to a saved server, restoring it on failure."""
    from mcp.types import ListToolsResult

    from gptme.config import Config, MCPConfig, MCPServerConfig
    from gptme.mcp.client import MCPClient
    from gptme.tools.mcp_adapter import unload_mcp_server

    name = "saved-remote"
    connection = (ListToolsResult(tools=[]), MagicMock())
    config = Config()
    config.user.mcp = MCPConfig(
        enabled=True,
        servers=[
            MCPServerConfig(
                name=name,
                enabled=True,
                url="https://old.example.net/sse",
                transport="sse",
            )
        ],
    )
    override = 'load saved-remote\n{"url": "https://new.example.net/mcp", "transport": "streamable-http"}'
    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=config),
        patch("gptme.tools.mcp.confirm", return_value=True),
        patch("gptme.mcp.client.build_oauth_provider", return_value=None),
        patch.object(
            MCPClient, "_setup_http_connection", return_value=connection
        ) as http,
        patch.object(
            MCPClient, "_setup_stdio_connection", return_value=connection
        ) as stdio,
    ):
        (message,) = execute_mcp(override, None, None)
        assert f"Successfully loaded server '{name}'" in message.content
        http.assert_awaited_once_with(
            "https://new.example.net/mcp", {}, use_sse=False, auth=None
        )
        stdio.assert_not_awaited()
        assert config.user.mcp is not None
        [saved] = config.user.mcp.servers
        assert saved.url == "https://new.example.net/mcp"
        assert saved.transport == "streamable-http"
        unload_mcp_server(name)

    # A failed connection restores the saved transport too.
    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=config),
        patch("gptme.tools.mcp.confirm", return_value=True),
        patch("gptme.mcp.client.build_oauth_provider", return_value=None),
        patch.object(
            MCPClient,
            "_setup_http_connection",
            side_effect=RuntimeError("connection failed"),
        ),
    ):
        (message,) = execute_mcp(override, None, None)
        assert f"Failed to load server '{name}'" in message.content
        assert config.user.mcp is not None
        [saved] = config.user.mcp.servers
        assert saved.url == "https://old.example.net/sse"
        assert saved.transport == "sse"


def test_execute_mcp_unknown_command():
    """Test handling of unknown MCP commands."""

    messages = list(execute_mcp("unknown-command", None, None))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Unknown MCP command" in messages[0].content


def test_execute_mcp_no_command():
    """Test handling when no command is provided."""

    messages = list(execute_mcp(None, None, None))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "No command provided" in messages[0].content


@pytest.mark.parametrize("query", ["database", "browser automation"])
def test_execute_mcp_search_with_json_args(query: str) -> None:
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
    ) as mock_search:
        # Test with valid JSON for registry and limit
        code = f'search {query}\n{{"registry": "official", "limit": "5"}}'
        messages = list(execute_mcp(code, None, None))

        cast(_CallAssertingMock, mock_search).assert_called_once_with(
            query, "official", 5
        )
        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_search_passes_full_query_to_registry() -> None:
    """Preserve the query through command parsing and the registry request."""
    response = MagicMock()
    registry_data = {
        "servers": [
            {"server": {"name": "browser-server", "description": "Browser automation"}}
        ]
    }

    with (
        patch.object(response, "json", return_value=registry_data),
        patch("gptme.mcp.registry.requests.get", return_value=response) as mock_get,
    ):
        code = 'search browser automation\n{"registry": "official", "limit": 5}'
        messages = list(execute_mcp(code, None, None))

    cast(_CallAssertingMock, mock_get).assert_called_once_with(
        "https://registry.modelcontextprotocol.io/v0/servers",
        params={"search": "browser automation", "limit": 5},
        timeout=10,
    )
    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "browser-server" in messages[0].content


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
        # Test with invalid JSON - should use defaults
        code = "search database\n{invalid json"
        messages = list(execute_mcp(code, None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "sqlite" in messages[0].content


def test_execute_mcp_info_no_server_name():
    """Test info command without server name."""

    messages = list(execute_mcp("info", None, None))

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
        messages = list(execute_mcp("info test-http-server", None, None))

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
        messages = list(execute_mcp("info test-stdio-server", None, None))

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
        messages = list(execute_mcp("info remote-server", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        # Server was found in registry, so no "not configured locally" prefix
        assert "remote-server" in content
        assert "A remote server" in content
        assert "official" in content


def test_execute_mcp_load_no_server_name():
    """Test load command without server name."""

    messages = list(execute_mcp("load", None, None))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: load <server-name>" in messages[0].content


def test_execute_mcp_load_cancelled():
    """Test load command when user cancels confirmation."""
    # Patch the hook-based confirm function to return False (user cancels)
    with patch("gptme.tools.mcp.confirm", return_value=False):
        messages = list(execute_mcp("load test-server", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Cancelled" in messages[0].content


def test_execute_mcp_load_with_config_override():
    """Test load command with config override (JSON args)."""
    with patch("gptme.tools.mcp.load_mcp_server", return_value="Server loaded"):
        code = 'load test-server\n{"enabled": true}'
        messages = list(execute_mcp(code, None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Server loaded" in messages[0].content


def test_execute_mcp_unload_no_server_name():
    """Test unload command without server name."""

    messages = list(execute_mcp("unload", None, None))

    assert len(messages) == 1
    assert messages[0].role == "system"
    assert "Usage: unload <server-name>" in messages[0].content


def test_execute_mcp_unload_cancelled():
    """Test unload command when user cancels confirmation."""
    # Patch the hook-based confirm function to return False (user cancels)
    with patch("gptme.tools.mcp.confirm", return_value=False):
        messages = list(execute_mcp("unload test-server", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Cancelled" in messages[0].content


def test_execute_mcp_exception_handling():
    """Test exception handling in execute_mcp."""
    with patch(
        "gptme.tools.mcp.search_mcp_servers",
        side_effect=RuntimeError("Test error"),
    ):
        messages = list(execute_mcp("search test", None, None))

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
        messages = list(execute_mcp("info unknown-server", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        content = messages[0].content
        # Should have both "not configured locally" prefix and "not found" message
        assert "not configured locally" in content
        assert "not found in registries" in content


def test_execute_mcp_unload_success():
    """Test successful unload command."""
    with patch("gptme.tools.mcp.unload_mcp_server", return_value="Server unloaded"):
        messages = list(execute_mcp("unload test-server", None, None))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "Server unloaded" in messages[0].content
