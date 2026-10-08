"""Tests for MCP adapter functionality."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gptme.config import Config, MCPConfig, MCPServerConfig
from gptme.mcp.registry import MCPServerInfo
from gptme.message import Message
from gptme.tools.mcp_adapter import (
    _dynamic_server_tool_names,
    _dynamic_servers,
    _mcp_clients,
    _restart_mcp_client,
    create_mcp_execute_function,
    create_mcp_tools,
    get_mcp_server_info,
    list_loaded_servers,
    load_mcp_server,
    search_mcp_servers,
    unload_mcp_server,
)


@pytest.fixture(autouse=True)
def clear_mcp_state():
    """Reset global MCP client state between tests."""
    from gptme.tools import clear_tools

    _dynamic_servers.clear()
    _dynamic_server_tool_names.clear()
    _mcp_clients.clear()
    clear_tools()
    yield
    _dynamic_servers.clear()
    _dynamic_server_tool_names.clear()
    _mcp_clients.clear()
    clear_tools()


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
    mock_tool.annotations = None

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
    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)

        assert len(tools) > 0
        assert any("test-server.test_tool" in tool.name for tool in tools)


def test_create_mcp_tools_annotations_default_to_empty_hints(
    mock_config, mock_mcp_client
):
    """Test create_mcp_tools leaves hints empty when annotations are absent."""
    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)

    assert tools[0].hints == frozenset()


def test_create_mcp_tools_extracts_hints_from_annotations(mock_config, mock_mcp_client):
    """Test create_mcp_tools maps MCP tool annotations into hint tags."""
    mock_tool = mock_mcp_client.connect.return_value[0].tools[0]
    mock_tool.annotations = SimpleNamespace(
        readOnlyHint=True,
        destructiveHint=None,
        idempotentHint=True,
        openWorldHint=False,
    )

    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)

    # readOnlyHint=True suppresses "destructive" even when destructiveHint is
    # absent (None would default to True per MCP spec, but a read-only tool
    # cannot be destructive by definition)
    assert tools[0].hints == frozenset({"read-only", "idempotent", "closed-world"})


def test_create_mcp_tools_connection_error(mock_config):
    """Test create_mcp_tools when server connection fails."""
    mock_client = MagicMock()
    mock_client.connect.side_effect = Exception("Connection failed")

    with patch("gptme.mcp.client.MCPClient", return_value=mock_client):
        # Should not raise, just skip the failed server
        tools = create_mcp_tools(mock_config)
        # May be empty if connection failed
        assert isinstance(tools, list)


def test_create_mcp_tools_strict_closes_earlier_clients():
    """A later strict-setup failure must close servers that already connected."""
    config = Config()
    servers = [
        MCPServerConfig(name="ok", enabled=True, command="ok-cmd"),
        MCPServerConfig(name="bad", enabled=True, command="bad-cmd"),
    ]
    config.user.mcp = MCPConfig(enabled=True, servers=servers)

    ok_client = MagicMock()
    ok_tools = MagicMock()
    ok_tools.tools = []
    ok_client.connect.return_value = (ok_tools, MagicMock())

    bad_client = MagicMock()
    bad_client.connect.side_effect = Exception("boom")

    registry: dict = {}
    with (
        patch("gptme.mcp.client.MCPClient", side_effect=[ok_client, bad_client]),
        pytest.raises(RuntimeError, match="Failed to connect to MCP server 'bad'"),
    ):
        create_mcp_tools(config, servers=servers, clients=registry, strict=True)

    ok_client.close.assert_called_once_with()
    bad_client.close.assert_called_once_with()
    assert registry == {}


@pytest.mark.parametrize("strict", [False, True])
def test_dynamic_spec_failure_preserves_borrowed_client(
    mock_config, mock_mcp_client, strict
):
    """Discovery isolates failures without closing a dynamically owned client."""
    earlier = MagicMock()
    earlier.connect.return_value = (SimpleNamespace(tools=[]), MagicMock())
    later = MagicMock()
    later.connect.return_value = mock_mcp_client.connect.return_value
    mock_config.user.mcp.servers = [
        MCPServerConfig(name="earlier", command="earlier"),
        *mock_config.user.mcp.servers,
        MCPServerConfig(name="later", command="later"),
    ]
    bad_tool = MagicMock()
    bad_tool.name = "bad"
    bad_tool.inputSchema = {"properties": None}
    mock_mcp_client.tools = SimpleNamespace(tools=[bad_tool])
    _dynamic_servers["test-server"] = mock_mcp_client

    with patch("gptme.mcp.client.MCPClient", side_effect=[earlier, later]):
        if strict:
            with pytest.raises(RuntimeError, match="test-server"):
                create_mcp_tools(mock_config, strict=True)
            earlier.close.assert_called_once_with()
            assert "earlier" not in _mcp_clients
            later.connect.assert_not_called()
        else:
            specs = create_mcp_tools(mock_config)
            assert [s.name for s in specs] == ["later.test_tool"]
            earlier.close.assert_not_called()
    assert _dynamic_servers["test-server"] is mock_mcp_client
    assert isinstance(mock_mcp_client, MagicMock)
    mock_mcp_client.close.assert_not_called()


def test_create_mcp_execute_function(mock_config):
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

    execute_fn = create_mcp_execute_function(
        "server.test_tool", mock_client, mock_config
    )

    # Test that execute function is callable
    assert callable(execute_fn)

    # Test execution with valid JSON
    result = execute_fn('{"param1": "value"}', None, None)
    # Handle both generator and list returns
    messages = list(result) if hasattr(result, "__iter__") else [result]
    assert len(messages) > 0


def test_mcp_execute_accepts_tool_format_kwargs(mock_config, mock_mcp_client):
    """Tool-format calls pass args in kwargs with no content.

    Regression for MCP tools failing with "No parameters provided" under
    ``--tool-format tool``: the adapter's execute() only read ``code``.
    """
    mock_mcp_client.call_tool.return_value = '{"datetime": "2026-10-02T00:00:00Z"}'
    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)
    tool = next(t for t in tools if t.name == "test-server.test_tool")

    # Tool format: content is None, arguments live in kwargs
    execute = tool.execute
    assert execute is not None
    result = execute(None, None, {"param1": "value"})
    messages = list(result) if hasattr(result, "__iter__") else [result]

    mock_mcp_client.call_tool.assert_called_once_with("test_tool", {"param1": "value"})
    assert any("datetime" in (m.content or "") for m in messages)


def test_tool_format_tooluse_executes_mcp_tool(mock_config, mock_mcp_client):
    """A tool-format ToolUse with kwargs and no content runs the MCP tool."""
    from gptme.tools import set_tools
    from gptme.tools.base import ToolUse

    mock_mcp_client.call_tool.return_value = '{"datetime": "2026-10-02T00:00:00Z"}'
    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)
    tool = next(t for t in tools if t.name == "test-server.test_tool")

    set_tools([tool])
    try:
        tool_use = ToolUse(
            tool.name,
            None,
            None,
            kwargs={"param1": "value"},
            _format="tool",
        )
        messages = list(tool_use.execute())
    finally:
        set_tools([])

    mock_mcp_client.call_tool.assert_called_once_with("test_tool", {"param1": "value"})
    assert any("datetime" in (m.content or "") for m in messages)


def test_mcp_execute_no_parameters_still_errors(mock_config, mock_mcp_client):
    """Empty tool-format calls keep the explicit no-parameters message."""
    with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
        tools = create_mcp_tools(mock_config)
    tool = next(t for t in tools if t.name == "test-server.test_tool")

    execute = tool.execute
    assert execute is not None
    result = execute(None, None, None)
    messages = list(result) if hasattr(result, "__iter__") else [result]

    assert len(messages) == 1
    assert "No parameters provided" in messages[0].content


def test_mcp_execute_parameterless_tool_format_runs(mock_config):
    """A parameterless tool-format call (`{}`) runs the tool with no arguments."""
    client = MagicMock()
    no_params_tool = MagicMock()
    no_params_tool.name = "no_params_tool"
    no_params_tool.description = "A tool with no parameters"
    # No required parameters: this is the call shape Greptile flagged.
    no_params_tool.inputSchema = {"type": "object", "properties": {}}
    no_params_tool.annotations = None
    tools_obj = MagicMock()
    tools_obj.tools = [no_params_tool]
    client.connect.return_value = (tools_obj, MagicMock())
    client.call_tool.return_value = '{"ok": true}'

    with patch("gptme.mcp.client.MCPClient", return_value=client):
        tools = create_mcp_tools(mock_config)
    tool = next(t for t in tools if t.name == "test-server.no_params_tool")

    execute = tool.execute
    assert execute is not None
    result = execute(None, None, {})
    messages = list(result) if hasattr(result, "__iter__") else [result]

    client.call_tool.assert_called_once_with("no_params_tool", {})
    assert any("ok" in (m.content or "") for m in messages)


def test_search_mcp_servers_all():
    """Test search_mcp_servers with 'all' registry."""
    mock_servers = [
        MCPServerInfo(name="server1", description="First", registry="official"),
        MCPServerInfo(name="server2", description="Second", registry="mcp.so"),
    ]

    mock_registry = MagicMock()
    mock_registry.search_all.return_value = mock_servers
    with patch("gptme.tools.mcp_adapter._get_registry", return_value=mock_registry):
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

    mock_registry = MagicMock()
    mock_registry.search_official_registry.return_value = mock_servers
    with patch(
        "gptme.tools.mcp_adapter._get_registry",
        return_value=mock_registry,
    ):
        result = search_mcp_servers("test", "official", 10)
        assert "official-server" in result


def test_search_mcp_servers_mcp_so():
    """Test search_mcp_servers with 'mcp.so' registry."""
    mock_servers = [
        MCPServerInfo(name="mcpso-server", description="MCP.so", registry="mcp.so"),
    ]

    mock_registry = MagicMock()
    mock_registry.search_mcp_so.return_value = mock_servers
    with patch("gptme.tools.mcp_adapter._get_registry", return_value=mock_registry):
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

    mock_registry = MagicMock()
    mock_registry.get_server_details.return_value = mock_server
    with patch("gptme.tools.mcp_adapter._get_registry", return_value=mock_registry):
        result = get_mcp_server_info("test-server")
        assert "test-server" in result
        assert "Test server" in result


def test_get_mcp_server_info_not_found():
    """Test get_mcp_server_info when server is not found."""
    mock_registry = MagicMock()
    mock_registry.get_server_details.return_value = None
    with patch("gptme.tools.mcp_adapter._get_registry", return_value=mock_registry):
        result = get_mcp_server_info("nonexistent")
        assert "not found" in result


def test_load_mcp_server_registers_toolspecs(mock_config, mock_mcp_client):
    """load_mcp_server must register ToolSpecs in the available-tools cache.

    Regression for gptme/gptme#4069: /mcp load reported success but the server's
    tools were never invocable because load_mcp_server() never built ToolSpecs.
    """
    from gptme.tools import _get_available_tools_cache, _set_available_tools_cache

    # Prime a warm (but empty) cache to simulate an already-running session.
    _set_available_tools_cache([])
    try:
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        ):
            load_mcp_server("test-server")

        cached = _get_available_tools_cache()
        assert cached is not None, "cache should still be warm after load"
        names = [t.name for t in cached]
        assert "test-server.test_tool" in names, (
            f"expected test-server.test_tool in cache after load, got {names}"
        )

        # Unload must remove the specs from the cache.
        unload_mcp_server("test-server")
        cached_after = _get_available_tools_cache()
        assert cached_after is not None
        names_after = [t.name for t in cached_after]
        assert "test-server.test_tool" not in names_after, (
            "test-server.test_tool should be removed from cache after unload"
        )
    finally:
        _set_available_tools_cache(None)  # restore cold cache for other tests


def test_load_mcp_server_activates_tools_in_context(mock_config, mock_mcp_client):
    """After load, the server's tools must be selectable/executable via get_tools()/get_tool(), not just listed in the cache."""
    from gptme import tools as tools_mod
    from gptme.tools import _set_available_tools_cache

    try:
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        ):
            result = load_mcp_server("test-server")
            assert "Successfully loaded" in result

            loaded = tools_mod.get_tools()
            assert "test-server.test_tool" in [t.name for t in loaded], (
                "tool should be active in the loaded context after load"
            )
            assert tools_mod.get_tool("test-server.test_tool") is not None

            unload_mcp_server("test-server")
            assert tools_mod.get_tool("test-server.test_tool") is None, (
                "tool should be gone from the loaded context after unload"
            )
    finally:
        tools_mod.clear_tools()  # reset context-local loaded tools even on failure
        _set_available_tools_cache(None)  # restore cold cache for other tests


def test_load_mcp_server_respects_session_allowlist(mock_config, mock_mcp_client):
    """Dynamic discovery must not widen a restricted executable toolset.

    The new specs are listed in the available-tools cache (discovery), but only
    tools matching the operator's session allowlist become executable, mirroring
    the init_tools() rule for startup MCP servers.
    """
    from gptme import tools as tools_mod
    from gptme.tools import (
        _get_available_tools_cache,
        _set_available_tools_cache,
        set_session_allowlist,
    )

    _set_available_tools_cache([])
    set_session_allowlist(["shell"])
    try:
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        ):
            result = load_mcp_server("test-server")
            assert "Successfully loaded" in result

            cached = _get_available_tools_cache()
            assert cached is not None
            assert "test-server.test_tool" in [t.name for t in cached], (
                "discovery should still list the new tool"
            )
            assert tools_mod.get_tool("test-server.test_tool") is None, (
                "a tool outside the session allowlist must not become executable"
            )

            unload_mcp_server("test-server")
            cached_after = _get_available_tools_cache()
            assert cached_after is not None
            assert "test-server.test_tool" not in [t.name for t in cached_after]
    finally:
        set_session_allowlist(None)
        tools_mod.clear_tools()
        _set_available_tools_cache(None)


def test_load_mcp_server_reenables_previously_unloaded_server(
    mock_config, mock_mcp_client
):
    """Reloading a server that unload left enabled=False must flip it back on."""
    from gptme import tools as tools_mod
    from gptme.tools import _set_available_tools_cache

    server = mock_config.user.mcp.servers[0]
    server.enabled = False
    try:
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch("gptme.tools.mcp_adapter.set_config") as set_cfg,
            patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        ):
            result = load_mcp_server("test-server")
            assert "Successfully loaded" in result
            assert server.enabled
            set_cfg.assert_called()
            assert "✓ enabled" in list_loaded_servers()
    finally:
        tools_mod.clear_tools()
        _set_available_tools_cache(None)


@pytest.mark.parametrize("failure_stage", ["connect", "spec_build"])
def test_failed_mcp_reload_preserves_disabled_state(
    mock_config, mock_mcp_client, failure_stage
):
    """A failed reload must not advertise the disabled server as enabled."""
    server = mock_config.user.mcp.servers[0]
    server.enabled = False
    if failure_stage == "connect":
        mock_mcp_client.connect.side_effect = RuntimeError("connection failed")

    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
        patch("gptme.tools.mcp_adapter.set_config"),
        patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        patch("gptme.tools.mcp_adapter._build_tool_specs_for_server") as build,
    ):
        if failure_stage == "spec_build":
            build.side_effect = ValueError("invalid schema")
        result = load_mcp_server("test-server")

        assert "Failed to load" in result
        assert not server.enabled
        assert "test-server" not in _dynamic_servers
        assert "✗ disabled" in list_loaded_servers()


def test_unload_exact_names_does_not_touch_prefix_sibling_servers():
    """Unloading 'foo' must not remove tools of a distinct 'foo.bar' server."""
    from gptme.tools import _get_available_tools_cache, _set_available_tools_cache
    from gptme.tools.base import ToolSpec
    from gptme.tools.mcp_adapter import _dynamic_server_tool_names

    def make_spec(name: str) -> ToolSpec:
        def execute(
            code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
        ) -> Message:
            return Message("system", "noop")

        return ToolSpec(name=name, desc="", execute=execute)

    _set_available_tools_cache([make_spec("foo.tool_a"), make_spec("foo.bar.tool_b")])
    _dynamic_servers["foo"] = MagicMock()
    _dynamic_servers["foo.bar"] = MagicMock()
    _dynamic_server_tool_names["foo"] = ["foo.tool_a"]
    _dynamic_server_tool_names["foo.bar"] = ["foo.bar.tool_b"]
    try:
        unload_mcp_server("foo")

        names = [t.name for t in _get_available_tools_cache() or []]
        assert "foo.tool_a" not in names
        assert "foo.bar.tool_b" in names, (
            "unloading 'foo' must not remove distinct 'foo.bar' server tools"
        )

        # cleanup other server
        unload_mcp_server("foo.bar")
    finally:
        _set_available_tools_cache(None)
        _dynamic_server_tool_names.clear()


def test_failed_spec_build_rolls_back_and_allows_retry(mock_config, mock_mcp_client):
    """A spec-building failure must not leave the server marked loaded; a retry must work."""
    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
        patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
        patch(
            "gptme.tools.mcp_adapter._build_tool_specs_for_server",
            side_effect=ValueError("bad schema"),
        ),
    ):
        result = load_mcp_server("test-server")
        assert "Failed to load" in result
        assert "test-server" not in _dynamic_servers, (
            "failed load must not leave the server in _dynamic_servers"
        )
        mock_mcp_client.close.assert_called_once()

    # Retry with working spec building succeeds (not blocked by "already loaded")
    with (
        patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
        patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client),
    ):
        result = load_mcp_server("test-server")
        assert "Successfully loaded" in result, (
            "retry after failed load must not be blocked by stale registration"
        )
        unload_mcp_server("test-server")


def test_spec_build_handles_boolean_property_schema(mock_config):
    """A boolean property schema (JSON Schema shorthand) must not crash spec building."""
    client = MagicMock()
    mock_tool = MagicMock()
    mock_tool.name = "flag_tool"
    mock_tool.description = "Toggles a flag"
    mock_tool.inputSchema = {
        "type": "object",
        "properties": {"flag": True},
        "required": [],
    }
    mock_tool.annotations = None
    mock_tools = MagicMock()
    mock_tools.tools = [mock_tool]
    client.connect.return_value = (mock_tools, MagicMock())

    from gptme.tools.mcp_adapter import _build_tool_specs_for_server

    specs = _build_tool_specs_for_server(
        mock_config.mcp.servers[0], mock_tools, mock_config, {}
    )
    assert len(specs) == 1
    assert specs[0].parameters[0].type == "string"


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
        patch("gptme.mcp.client.MCPClient", return_value=mock_client),
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
    # The subprocess/stdio transport must not outlive the unload.
    mock_client.close.assert_called_once_with()


def test_session_client_retry_stays_in_session_registry(mock_config):
    """Connection recovery must replace only the supplied session client."""
    from gptme.mcp.client import MCPClient
    from gptme.tools.mcp_adapter import _call_mcp_tool_with_retry

    old_client = MagicMock(spec=MCPClient)
    old_client.call_tool.side_effect = RuntimeError("connection closed")
    replacement = MagicMock(spec=MCPClient)
    replacement.call_tool.return_value = "recovered"
    clients: dict[str, MCPClient] = {"test-server": old_client}

    with patch("gptme.mcp.client.MCPClient", return_value=replacement):
        result = _call_mcp_tool_with_retry(
            "test-server", "test_tool", {}, mock_config, clients=clients
        )

    assert result == "recovered"
    old_client.close.assert_called_once_with()
    replacement.connect.assert_called_once_with("test-server")
    assert clients == {"test-server": replacement}
    assert "test-server" not in _mcp_clients


def test_restart_mcp_client_survives_cleanup_failure_and_reconnects(mock_config):
    """Test restart tolerates cleanup failures from the old client."""

    close_calls: list[str] = []

    class BrokenLoop:
        def close(self) -> None:
            close_calls.append("close")
            raise RuntimeError("cleanup boom")

    class OldClient:
        stack = None
        loop = BrokenLoop()

    new_client = MagicMock()
    new_client.connect.return_value = (MagicMock(), MagicMock())

    _mcp_clients["test-server"] = OldClient()  # type: ignore[assignment]

    with patch("gptme.mcp.client.MCPClient", return_value=new_client):
        restarted = _restart_mcp_client("test-server", mock_config)

    assert close_calls == ["close"]
    assert restarted is new_client
    assert _mcp_clients["test-server"] is new_client
    new_client.connect.assert_called_once_with("test-server")


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


# ============================================================================
# MCP-to-gptme elicitation bridge tests
# ============================================================================

# ElicitRequestFormParams was added in mcp >= 1.22.0 (split from ElicitRequestParams)
# In older versions, ElicitRequestParams is a Union TypeAlias, not instantiable directly.
# These tests require mcp >= 1.22.0 with ElicitRequestFormParams as a concrete class.
_has_mcp_elicitation = False
_ElicitFormParams = None
try:
    import mcp.types as _mcp_types

    if hasattr(_mcp_types, "ElicitRequestFormParams"):
        _has_mcp_elicitation = True
        _ElicitFormParams = _mcp_types.ElicitRequestFormParams
except ImportError:
    pass


@pytest.mark.skipif(
    not _has_mcp_elicitation,
    reason="MCP elicitation (ElicitRequestFormParams) requires mcp >= 1.22.0",
)
class TestMCPElicitationBridge:
    """Tests for MCP elicitation → gptme elicitation translation."""

    def test_simple_text_request(self):
        """MCP request without schema maps to text elicitation."""
        from gptme.tools.mcp_adapter import _mcp_params_to_elicitation_request

        assert _ElicitFormParams is not None
        params = _ElicitFormParams(message="Enter your name", requestedSchema={})
        request = _mcp_params_to_elicitation_request(params, "test-server")

        assert request.type == "text"
        assert "test-server" in request.prompt
        assert "Enter your name" in request.prompt

    def test_schema_request_maps_to_form(self):
        """MCP request with schema maps to form elicitation with FormFields."""
        from gptme.tools.mcp_adapter import _mcp_params_to_elicitation_request

        assert _ElicitFormParams is not None
        params = _ElicitFormParams(
            message="Configure settings",
            requestedSchema={
                "properties": {
                    "name": {"type": "string", "description": "Your name"},
                    "age": {"type": "integer", "description": "Your age"},
                    "active": {"type": "boolean", "description": "Is active?"},
                },
                "required": ["name"],
            },
        )
        request = _mcp_params_to_elicitation_request(params, "test-server")

        assert request.type == "form"
        assert request.fields is not None
        assert len(request.fields) == 3

        # Check field mapping
        name_field = next(f for f in request.fields if f.name == "name")
        assert name_field.type == "text"
        assert name_field.required is True

        age_field = next(f for f in request.fields if f.name == "age")
        assert age_field.type == "number"

        active_field = next(f for f in request.fields if f.name == "active")
        assert active_field.type == "boolean"

    def test_cancelled_response_to_mcp(self):
        """Cancelled ElicitationResponse maps to MCP cancel action."""
        from gptme.hooks.elicitation import ElicitationResponse
        from gptme.tools.mcp_adapter import _elicitation_response_to_mcp_result

        response = ElicitationResponse.cancel()
        result = _elicitation_response_to_mcp_result(response)

        assert result.action == "cancel"
        assert result.content is None

    def test_none_value_response_to_mcp(self):
        """None value in response maps to MCP decline action."""
        from gptme.hooks.elicitation import ElicitationResponse
        from gptme.tools.mcp_adapter import _elicitation_response_to_mcp_result

        response = ElicitationResponse(value=None, cancelled=False)
        result = _elicitation_response_to_mcp_result(response)

        assert result.action == "decline"

    def test_form_json_response_to_mcp(self):
        """JSON form response maps to MCP accept with parsed content."""
        import json

        from gptme.hooks.elicitation import ElicitationResponse
        from gptme.tools.mcp_adapter import _elicitation_response_to_mcp_result

        form_data = {"name": "Bob", "age": 42}
        response = ElicitationResponse.text(json.dumps(form_data))
        result = _elicitation_response_to_mcp_result(response)

        assert result.action == "accept"
        assert result.content == form_data

    def test_text_response_to_mcp(self):
        """Plain text response wraps in value dict for MCP."""
        from gptme.hooks.elicitation import ElicitationResponse
        from gptme.tools.mcp_adapter import _elicitation_response_to_mcp_result

        response = ElicitationResponse.text("hello")
        result = _elicitation_response_to_mcp_result(response)

        assert result.action == "accept"
        assert result.content == {"value": "hello"}

    def test_schema_with_defaults(self):
        """Schema properties with defaults are passed through to FormFields."""
        from gptme.tools.mcp_adapter import _mcp_params_to_elicitation_request

        assert _ElicitFormParams is not None
        params = _ElicitFormParams(
            message="Settings",
            requestedSchema={
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Port number",
                        "default": 8080,
                    },
                },
                "required": [],
            },
        )
        request = _mcp_params_to_elicitation_request(params, "test-server")

        assert request.fields is not None
        port_field = request.fields[0]
        assert port_field.default == "8080"
        assert port_field.required is False


def test_dynamic_tools_do_not_mutate_inherited_context():
    from contextvars import copy_context

    from gptme import tools as tools_mod
    from gptme.tools.base import ToolSpec

    spec = ToolSpec(
        name="context.tool", desc="", execute=lambda *_: Message("system", "ok")
    )
    tools_mod.clear_tools()
    try:
        child = copy_context()
        child.run(tools_mod.load_dynamic_tool_specs, [spec])
        assert child.run(tools_mod.get_tool, spec.name) is not None
        assert tools_mod.get_tool(spec.name) is None

        tools_mod.load_dynamic_tool_specs([spec])
        child = copy_context()
        child.run(tools_mod.unload_dynamic_tool_specs, [spec.name])
        assert child.run(tools_mod.get_tool, spec.name) is None
        assert tools_mod.get_tool(spec.name) is not None
    finally:
        tools_mod.clear_tools()


def test_load_startup_server_does_not_replace_connection(mock_config, mock_mcp_client):
    from gptme import tools as tools_mod

    tools_mod.clear_tools()
    try:
        with patch("gptme.mcp.client.MCPClient", return_value=mock_mcp_client):
            specs = create_mcp_tools(mock_config)
        tools_mod.load_dynamic_tool_specs(specs)
        tools_mod._set_available_tools_cache(specs)
        original_command = mock_config.mcp.servers[0].command
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch("gptme.mcp.client.MCPClient") as client_factory,
        ):
            result = load_mcp_server("test-server", {"command": "other-server"})
            assert "already loaded" in result
            client_factory.assert_not_called()
            assert "not loaded" in unload_mcp_server("test-server")
        assert mock_config.mcp.servers[0].command == original_command
        assert tools_mod.get_tool(specs[0].name) is not None
        assert tools_mod._get_available_tools_cache() == specs
        assert "test-server" not in _dynamic_servers
    finally:
        tools_mod.clear_tools()


@pytest.mark.parametrize("content", ['{"flag":', '{"flag": true}'])
def test_boolean_schema_execution_errors_are_messages(
    mock_config, mock_mcp_client, content
):
    tool = mock_mcp_client.connect.return_value[0].tools[0]
    tool.inputSchema = {"type": "object", "properties": {"flag": True}}
    mock_mcp_client.tools = mock_mcp_client.connect.return_value[0]
    execute = create_mcp_execute_function(
        tool.name, "test-server", mock_config, clients={"test-server": mock_mcp_client}
    )

    def confirm(code, args, kwargs, *, execute_fn, **options):
        yield from execute_fn(code)

    with (
        patch("gptme.tools.mcp_adapter.execute_with_confirmation", side_effect=confirm),
        patch(
            "gptme.tools.mcp_adapter._call_mcp_tool_with_retry",
            side_effect=ValueError("call failed"),
        ),
    ):
        result = execute(content, None, None)
        assert not isinstance(result, Message)
        messages = list(result)
    assert len(messages) == 1
    assert "Error executing tool:" in messages[0].content
    assert "flag: No description (Optional)" in messages[0].content
    if content == '{"flag":':
        assert "valid JSON object" in messages[0].content


def test_cold_discovery_reuses_dynamic_connection(mock_config, mock_mcp_client):
    from gptme import tools as tools_mod

    tools_mod.clear_tools()
    mock_mcp_client.tools = mock_mcp_client.connect.return_value[0]
    try:
        with (
            patch("gptme.tools.mcp_adapter.get_config", return_value=mock_config),
            patch(
                "gptme.mcp.client.MCPClient", return_value=mock_mcp_client
            ) as factory,
        ):
            assert "Successfully loaded" in load_mcp_server("test-server")
            specs = create_mcp_tools(mock_config)
            assert [spec.name for spec in specs] == ["test-server.test_tool"]
            assert factory.call_count == 1
            assert "test-server" not in _mcp_clients
            assert "Successfully unloaded" in unload_mcp_server("test-server")
            assert "Successfully loaded" in load_mcp_server("test-server")
            assert factory.call_count == 2
            unload_mcp_server("test-server")
    finally:
        tools_mod.clear_tools()


def test_create_mcp_tools_failed_spec_build_removes_closed_client():
    """A spec-building failure in default (non-strict) mode must not leave the
    closed client in the registry: a stale entry makes load_mcp_server()
    report 'already loaded' forever, blocking any retry."""
    config = Config()
    servers = [MCPServerConfig(name="bad", enabled=True, command="bad-cmd")]
    config.user.mcp = MCPConfig(enabled=True, servers=servers)

    client = MagicMock()
    client.connect.return_value = (MagicMock(), MagicMock())

    registry: dict = {}
    with (
        patch("gptme.mcp.client.MCPClient", return_value=client),
        patch(
            "gptme.tools.mcp_adapter._build_tool_specs_for_server",
            side_effect=ValueError("bad schema"),
        ),
    ):
        tools = create_mcp_tools(config, servers=servers, clients=registry)

    assert tools == []
    client.close.assert_called_once_with()
    assert registry == {}, "closed client must not stay registered as loaded"


def test_copy_context_unload_does_not_clean_parent_tool_list():
    """Regression guard for the ContextVar contract on load/unload_mcp_server.

    When unload_mcp_server() is called from a copy_context() child after the
    server was loaded in the parent, the parent's ContextVar-backed tool list
    retains the tool — creating a dangling reference.  This documents the
    unsupported scenario described in the load/unload_mcp_server docstrings.
    """
    import contextvars

    from gptme.tools import _loaded_tools_var

    # Simulate a loaded MCP tool in the parent context by inserting directly
    # into the shared dicts and the ContextVar-backed tool list.
    mock_client = MagicMock()
    server_name = "ctx-test-server"
    tool_name = "ctx_test_tool"

    _dynamic_servers[server_name] = mock_client
    _dynamic_server_tool_names[server_name] = [tool_name]

    fake_spec = MagicMock()
    fake_spec.name = tool_name
    _loaded_tools_var.set([*(_loaded_tools_var.get() or []), fake_spec])

    parent_names_before = {t.name for t in (_loaded_tools_var.get() or [])}
    assert tool_name in parent_names_before

    # Attempt to unload from a copy_context() child
    ctx = contextvars.copy_context()
    ctx.run(lambda: unload_mcp_server(server_name))

    # The shared dicts are modified (server is gone) — mixed state
    assert server_name not in _dynamic_servers

    # But the parent's ContextVar-backed tool list still contains the tool
    # because copy_context() child ContextVar mutations do not propagate back
    parent_names_after = {t.name for t in (_loaded_tools_var.get() or [])}
    assert tool_name in parent_names_after, (
        "parent ContextVar must retain tool after child unload: "
        "cross-context unload is unsupported (see load/unload_mcp_server docstring)"
    )
