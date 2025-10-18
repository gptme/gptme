"""Tests for the MCP-related gptme-util CLI commands."""

from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from gptme.util.cli import main


@pytest.fixture
def mock_config(mocker):
    """Mock configuration with MCP settings."""
    config = Mock()
    config.mcp.enabled = True
    config.mcp.servers = []
    mocker.patch("gptme.util.cli.get_config", return_value=config)
    return config


@pytest.fixture
def mock_mcp_client(mocker):
    """Mock MCPClient for connection testing."""
    client_mock = Mock()
    mocker.patch("gptme.util.cli.MCPClient", return_value=client_mock)
    return client_mock


@pytest.fixture
def mock_registry(mocker):
    """Mock MCPRegistry for search and info commands."""
    registry_mock = Mock()
    mocker.patch("gptme.mcp.registry.MCPRegistry", return_value=registry_mock)
    return registry_mock


class TestMCPList:
    """Tests for 'mcp list' command."""

    def test_mcp_disabled(self, mock_config):
        """Test when MCP is disabled."""
        mock_config.mcp.enabled = False
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "MCP is disabled" in result.output

    def test_no_servers_configured(self, mock_config):
        """Test when no servers are configured."""
        mock_config.mcp.servers = []
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "No MCP servers configured" in result.output

    def test_list_disabled_server(self, mock_config):
        """Test listing a disabled server."""
        server = Mock()
        server.name = "test-server"
        server.enabled = False
        server.is_http = False
        mock_config.mcp.servers = [server]

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "test-server" in result.output
        assert "Disabled" in result.output

    def test_list_connected_server(self, mock_config, mock_mcp_client):
        """Test listing a server with successful connection."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        mock_config.mcp.servers = [server]

        # Mock successful connection
        tools_mock = Mock()
        tool1 = Mock()
        tool1.name = "tool1"
        tool2 = Mock()
        tool2.name = "tool2"
        tools_mock.tools = [tool1, tool2]
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "test-server" in result.output
        assert "Connected" in result.output
        assert "2 tools available" in result.output
        assert "tool1" in result.output

    def test_list_connection_failure(self, mock_config, mock_mcp_client):
        """Test listing a server with connection failure."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        mock_config.mcp.servers = [server]

        # Mock connection failure
        mock_mcp_client.connect.side_effect = Exception("Connection refused")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "test-server" in result.output
        assert "Connection failed" in result.output
        assert "Connection refused" in result.output

    def test_list_http_server(self, mock_config, mock_mcp_client):
        """Test listing an HTTP server."""
        server = Mock()
        server.name = "http-server"
        server.enabled = True
        server.is_http = True
        mock_config.mcp.servers = [server]

        tools_mock = Mock()
        tools_mock.tools = []
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "http-server" in result.output
        assert "HTTP" in result.output

    def test_list_multiple_servers(self, mock_config, mock_mcp_client):
        """Test listing multiple servers with mixed states."""
        server1 = Mock()
        server1.name = "enabled-server"
        server1.enabled = True
        server1.is_http = False

        server2 = Mock()
        server2.name = "disabled-server"
        server2.enabled = False
        server2.is_http = False

        mock_config.mcp.servers = [server1, server2]

        tools_mock = Mock()
        tools_mock.tools = []
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "2 MCP server(s)" in result.output
        assert "enabled-server" in result.output
        assert "disabled-server" in result.output

    def test_list_many_tools(self, mock_config, mock_mcp_client):
        """Test listing server with many tools (shows truncation)."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        mock_config.mcp.servers = [server]

        # Create 5 tools
        tools_mock = Mock()
        # Create tools with .name attribute
        tools = []
        for i in range(5):
            tool = Mock()
            tool.name = f"tool{i}"
            tools.append(tool)
        tools_mock.tools = tools
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "list"])
        assert result.exit_code == 0
        assert "5 tools available" in result.output
        assert "+2 more" in result.output  # Shows first 3, then "+2 more"


class TestMCPTest:
    """Tests for 'mcp test' command."""

    def test_mcp_disabled(self, mock_config):
        """Test when MCP is disabled."""
        mock_config.mcp.enabled = False
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "test-server"])
        assert result.exit_code == 0
        assert "MCP is disabled" in result.output

    def test_server_not_found(self, mock_config):
        """Test when server is not in config."""
        mock_config.mcp.servers = []
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "nonexistent"])
        assert result.exit_code == 0
        assert "not found in config" in result.output

    def test_server_disabled(self, mock_config):
        """Test when server is disabled."""
        server = Mock()
        server.name = "test-server"
        server.enabled = False
        mock_config.mcp.servers = [server]

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "test-server"])
        assert result.exit_code == 0
        assert "is disabled" in result.output

    def test_successful_connection(self, mock_config, mock_mcp_client):
        """Test successful connection to server."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        mock_config.mcp.servers = [server]

        # Mock successful connection with tools
        tools_mock = Mock()
        tool1 = Mock()
        tool1.name = "tool1"
        tool1.description = "First tool"
        tool2 = Mock()
        tool2.name = "tool2"
        tool2.description = None  # No description
        tools_mock.tools = [tool1, tool2]
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "test-server"])
        assert result.exit_code == 0
        assert "Testing test-server" in result.output
        assert "Connected successfully" in result.output
        assert "tool1: First tool" in result.output
        assert "tool2: No description" in result.output

    def test_connection_failure(self, mock_config, mock_mcp_client):
        """Test connection failure."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        mock_config.mcp.servers = [server]

        mock_mcp_client.connect.side_effect = Exception("Connection timeout")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "test-server"])
        assert result.exit_code == 0
        assert "Connection failed" in result.output
        assert "Connection timeout" in result.output

    def test_http_server_type(self, mock_config, mock_mcp_client):
        """Test that HTTP server type is shown."""
        server = Mock()
        server.name = "http-server"
        server.enabled = True
        server.is_http = True
        mock_config.mcp.servers = [server]

        tools_mock = Mock()
        tools_mock.tools = []
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "test", "http-server"])
        assert result.exit_code == 0
        assert "HTTP" in result.output


class TestMCPInfo:
    """Tests for 'mcp info' command."""

    def test_local_configured_server(self, mock_config, mock_mcp_client):
        """Test info for locally configured server."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        server.command = "python"
        server.args = ["server.py", "--port", "8080"]
        server.env = {"KEY": "value"}
        mock_config.mcp.servers = [server]

        # Mock successful connection
        tools_mock = Mock()
        tools_mock.tools = [Mock(), Mock()]
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "test-server"])
        assert result.exit_code == 0
        assert "MCP Server: test-server" in result.output
        assert "Type: stdio" in result.output
        assert "Enabled: ✅" in result.output
        assert "Command: python" in result.output
        assert "Args: server.py --port 8080" in result.output
        assert "Environment: 1 variables" in result.output
        assert "Connected" in result.output

    def test_local_http_server(self, mock_config, mock_mcp_client):
        """Test info for locally configured HTTP server."""
        server = Mock()
        server.name = "http-server"
        server.enabled = True
        server.is_http = True
        server.url = "http://localhost:8080"
        server.headers = {"Authorization": "Bearer token"}
        mock_config.mcp.servers = [server]

        tools_mock = Mock()
        tools_mock.tools = []
        session_mock = Mock()
        mock_mcp_client.connect.return_value = (tools_mock, session_mock)

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "http-server"])
        assert result.exit_code == 0
        assert "Type: HTTP" in result.output
        assert "URL: http://localhost:8080" in result.output
        assert "Headers: 1 configured" in result.output
        server.env = {}

    def test_local_disabled_server(self, mock_config):
        """Test info for disabled local server."""
        server = Mock()
        server.name = "test-server"
        server.enabled = False
        server.is_http = False
        server.command = "python"
        server.args = []
        server.env = {}
        mock_config.mcp.servers = [server]

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "test-server"])
        assert result.exit_code == 0
        assert "Enabled: ❌" in result.output
        # Should not test connection for disabled server
        assert "Testing connection" not in result.output

    def test_local_connection_failure(self, mock_config, mock_mcp_client):
        """Test info when local server connection fails."""
        server = Mock()
        server.name = "test-server"
        server.enabled = True
        server.is_http = False
        server.command = "python"
        server.args = []
        server.env = {}
        mock_config.mcp.servers = [server]

        mock_mcp_client.connect.side_effect = Exception("Connection failed")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "test-server"])
        assert result.exit_code == 0
        assert "Connection failed" in result.output

    def test_registry_search_success(self, mock_config, mock_registry, mocker):
        """Test info searches registry when not found locally."""
        mock_config.mcp.servers = []

        # Mock registry search
        registry_server = {"name": "registry-server", "description": "From registry"}
        mock_registry.get_server_details.return_value = registry_server

        # Mock format function
        mocker.patch(
            "gptme.mcp.registry.format_server_details",
            return_value="Formatted server details",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "registry-server"])
        assert result.exit_code == 0
        assert "not configured locally" in result.output
        assert "Searching registries" in result.output
        assert "Formatted server details" in result.output

    def test_registry_search_not_found(self, mock_config, mock_registry):
        """Test info when server not found in registry either."""
        mock_config.mcp.servers = []
        mock_registry.get_server_details.return_value = None

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "nonexistent"])
        assert result.exit_code == 0
        assert "not found in registries either" in result.output
        assert "Try searching" in result.output

    def test_registry_search_error(self, mock_config, mock_registry):
        """Test info when registry search fails."""
        mock_config.mcp.servers = []
        mock_registry.get_server_details.side_effect = Exception("Network error")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "info", "test"])
        assert result.exit_code == 0
        assert "Registry search failed" in result.output


class TestMCPSearch:
    """Tests for 'mcp search' command."""

    def test_search_all_registries(self, mock_registry, mocker):
        """Test search across all registries."""
        results = [
            {"name": "server1", "description": "First"},
            {"name": "server2", "description": "Second"},
        ]
        mock_registry.search_all.return_value = results

        mocker.patch(
            "gptme.mcp.registry.format_server_list",
            return_value="Formatted results",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search", "test"])
        assert result.exit_code == 0
        assert "Searching all registries" in result.output
        assert "Formatted results" in result.output
        mock_registry.search_all.assert_called_once_with("test", 10)

    def test_search_official_registry(self, mock_registry, mocker):
        """Test search in official registry only."""
        results = [{"name": "official-server"}]
        mock_registry.search_official_registry.return_value = results

        mocker.patch(
            "gptme.mcp.registry.format_server_list",
            return_value="Official results",
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["mcp", "search", "--registry", "official", "test"]
        )
        assert result.exit_code == 0
        assert "Searching official registry" in result.output
        mock_registry.search_official_registry.assert_called_once_with("test", 10)

    def test_search_mcp_so_registry(self, mock_registry, mocker):
        """Test search in mcp.so registry."""
        results = [{"name": "mcp-server"}]
        mock_registry.search_mcp_so.return_value = results

        mocker.patch(
            "gptme.mcp.registry.format_server_list",
            return_value="MCP.so results",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search", "-r", "mcp.so", "test"])
        assert result.exit_code == 0
        assert "Searching mcp.so registry" in result.output
        mock_registry.search_mcp_so.assert_called_once_with("test", 10)

    def test_search_with_limit(self, mock_registry, mocker):
        """Test search with custom limit."""
        results: list[dict] = []
        mock_registry.search_all.return_value = results

        mocker.patch("gptme.mcp.registry.format_server_list", return_value="")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search", "-n", "5", "test"])
        assert result.exit_code == 0
        mock_registry.search_all.assert_called_once_with("test", 5)

    def test_search_no_results(self, mock_registry):
        """Test search with no results."""
        mock_registry.search_all.return_value = []

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search", "nonexistent"])
        assert result.exit_code == 0
        assert "No servers found" in result.output

    def test_search_empty_query(self, mock_registry, mocker):
        """Test search with empty query (lists all)."""
        results = [{"name": "server1"}, {"name": "server2"}]
        mock_registry.search_all.return_value = results

        mocker.patch(
            "gptme.mcp.registry.format_server_list",
            return_value="All servers",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search"])
        assert result.exit_code == 0
        assert "All servers" in result.output

    def test_search_error(self, mock_registry):
        """Test search when registry fails."""
        mock_registry.search_all.side_effect = Exception("Network timeout")

        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "search", "test"])
        assert result.exit_code == 0
        assert "Search failed" in result.output
        assert "Network timeout" in result.output
