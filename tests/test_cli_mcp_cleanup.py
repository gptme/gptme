"""Every MCP CLI command must close the client it creates.

Real HTTP probes showed that ``gptme-util mcp list/test/info`` retained one
server session per successful invocation. These tests pin the caller lifecycle
contract on success and failure: a created client is always closed, and paths
that never create one (disabled server, declined consent) stay client-free.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from gptme.cli.util import main

COMMANDS = [
    pytest.param(["mcp", "list"], id="list"),
    pytest.param(["mcp", "test", "demo"], id="test"),
    pytest.param(["mcp", "info", "demo"], id="info"),
]


def _configure(mocker, *, server_enabled: bool = True) -> Mock:
    """Install a single stdio server config for the CLI to read."""
    server = Mock()
    server.name = "demo"
    server.enabled = server_enabled
    server.is_http = False
    server.command = "demo-server"
    server.args = []
    server.env = {}

    config = Mock()
    config.user = None
    config.project = None
    config.chat = None
    config.mcp.enabled = True
    config.mcp.servers = [server]
    mocker.patch("gptme.cli.cmd_mcp.Config.from_workspace", return_value=config)
    return config


def _install_client(mocker, *, connect_error: Exception | None = None) -> Mock:
    """Patch ``MCPClient`` so the command gets a spy instead of a subprocess."""
    client = Mock()
    if connect_error is None:
        client.connect.return_value = (SimpleNamespace(tools=[]), None)
    else:
        client.connect.side_effect = connect_error
    mocker.patch("gptme.cli.cmd_mcp.MCPClient", return_value=client)
    return client


@pytest.mark.parametrize("command", COMMANDS)
def test_command_closes_client_on_success(command, mocker):
    _configure(mocker)
    client = _install_client(mocker)

    result = CliRunner().invoke(main, command)

    assert result.exit_code == 0, result.output
    assert "Connected" in result.output
    client.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("command", "expected_exit"),
    [
        pytest.param(["mcp", "list"], 0, id="list"),
        pytest.param(["mcp", "test", "demo"], 1, id="test"),
        pytest.param(["mcp", "info", "demo"], 0, id="info"),
    ],
)
def test_command_closes_client_on_connect_failure(command, expected_exit, mocker):
    _configure(mocker)
    client = _install_client(mocker, connect_error=Exception("boom"))

    result = CliRunner().invoke(main, command)

    assert result.exit_code == expected_exit, result.output
    assert "Connection failed" in result.output
    client.close.assert_called_once_with()


@pytest.mark.parametrize("command", COMMANDS)
def test_disabled_server_creates_no_client(command, mocker):
    _configure(mocker, server_enabled=False)
    factory = mocker.patch("gptme.cli.cmd_mcp.MCPClient")

    result = CliRunner().invoke(main, command)

    factory.assert_not_called()
    assert result.exit_code in (0, 1), result.output


@pytest.mark.parametrize(
    ("command", "expected_exit"),
    [
        pytest.param(["mcp", "list"], 0, id="list"),
        pytest.param(["mcp", "test", "demo"], 1, id="test"),
        pytest.param(["mcp", "info", "demo"], 0, id="info"),
    ],
)
def test_declined_project_consent_creates_no_client(command, expected_exit, mocker):
    _configure(mocker)
    mocker.patch("gptme.cli.cmd_mcp._confirm_project_connection", return_value=False)
    factory = mocker.patch("gptme.cli.cmd_mcp.MCPClient")

    result = CliRunner().invoke(main, command)

    assert result.exit_code == expected_exit, result.output
    assert "not approved" in result.output
    factory.assert_not_called()
