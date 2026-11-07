"""MCP connection configuration stays independent of the inherited process env."""

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from gptme.config import MCPServerConfig
from gptme.mcp.client import MCPClient


def make_client(server: MCPServerConfig) -> Any:
    """Exercise connect without opening a transport or private event loop."""
    client: Any = MCPClient.__new__(MCPClient)
    client.config = SimpleNamespace(mcp=SimpleNamespace(enabled=True, servers=[server]))
    client._setup_stdio_connection = Mock()
    client._setup_http_connection = Mock()
    client._run_async = Mock(return_value=(SimpleNamespace(tools=[]), object()))
    return client


def test_explicit_server_env_overrides_inherited_collision() -> None:
    server = MCPServerConfig(
        name="fixture", command="unused", env={"FIXTURE": "explicit"}
    )
    client = make_client(server)
    with patch.dict(
        os.environ, {"FIXTURE": "ambient", "INHERITED_ONLY": "kept"}, clear=True
    ):
        client.connect("fixture")
    params = client._setup_stdio_connection.call_args.args[0]
    assert params.env["FIXTURE"] == "explicit"
    assert params.env["INHERITED_ONLY"] == "kept"


def test_connect_does_not_mutate_configured_env() -> None:
    server = MCPServerConfig(
        name="fixture", command="unused", env={"FIXTURE": "explicit"}
    )
    configured = server.env
    client = make_client(server)
    with patch.dict(os.environ, {"INHERITED_ONLY": "kept"}, clear=True):
        client.connect("fixture")
    assert server.env is configured
    assert configured == {"FIXTURE": "explicit"}


def test_reconnect_drops_removed_ambient_keys() -> None:
    server = MCPServerConfig(
        name="fixture", command="unused", env={"FIXTURE": "explicit"}
    )
    client = make_client(server)
    with patch.dict(os.environ, {"TEMPORARY": "first"}, clear=True):
        client.connect("fixture")
    first_params = client._setup_stdio_connection.call_args.args[0]
    with patch.dict(os.environ, {"CURRENT": "second"}, clear=True):
        client.connect("fixture")
    second_params = client._setup_stdio_connection.call_args.args[0]
    assert first_params.env == {"FIXTURE": "explicit", "TEMPORARY": "first"}
    assert second_params.env == {"FIXTURE": "explicit", "CURRENT": "second"}


@pytest.mark.parametrize("configured", [None, {}])
def test_empty_server_env_inherits_without_mutation(
    configured: Any,
) -> None:
    server = MCPServerConfig(name="fixture", command="unused", env=configured)
    client = make_client(server)
    with patch.dict(os.environ, {"INHERITED_ONLY": "kept"}, clear=True):
        client.connect("fixture")
    params = client._setup_stdio_connection.call_args.args[0]
    assert params.env == {"INHERITED_ONLY": "kept"}
    assert server.env == configured


def test_connect_does_not_mutate_ambient_environment() -> None:
    server = MCPServerConfig(
        name="fixture", command="unused", env={"FIXTURE": "explicit"}
    )
    client = make_client(server)
    with patch.dict(os.environ, {"INHERITED_ONLY": "kept"}, clear=True):
        client.connect("fixture")
        assert dict(os.environ) == {"INHERITED_ONLY": "kept"}


def test_http_connect_forwards_url_and_headers_unchanged() -> None:
    server = MCPServerConfig(
        name="fixture",
        url="https://example.invalid/mcp",
        headers={"Authorization": "Bearer synthetic"},
        env={"FIXTURE": "explicit"},
    )
    client = make_client(server)
    with patch.dict(os.environ, {"FIXTURE": "ambient"}, clear=True):
        tools, session = client.connect("fixture")
    client._setup_http_connection.assert_called_once_with(server.url, server.headers)
    client._setup_stdio_connection.assert_not_called()
    assert server.env == {"FIXTURE": "explicit"}
    assert tools.tools == []
    assert session is client._run_async.return_value[1]
