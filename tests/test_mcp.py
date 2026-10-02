import os
import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
import tomlkit

# Import the minimal set of required modules
from gptme.config import MCPConfig, MCPServerConfig, UserConfig


def test_mcp_cli_commands(monkeypatch):
    """Test MCP CLI command logic without hitting the live registry."""
    from click.testing import CliRunner

    from gptme.cli.cmd_mcp import mcp_info
    from gptme.mcp.registry import MCPRegistry

    monkeypatch.setattr(
        MCPRegistry,
        "get_server_details",
        lambda self, server_name: None,
    )

    # Test with mock data - this would normally use the config system
    runner = CliRunner()

    # Test info command with non-existent server - should exit 1 (server not found anywhere)
    result = runner.invoke(mcp_info, ["nonexistent"])
    assert result.exit_code == 1
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


def test_mcp_roots_management():
    """Test MCP roots management methods"""
    import mcp.types as types

    from gptme.mcp.client import MCPClient

    # Create client without connecting to any server
    client = MCPClient()

    # Test initial state - no roots
    assert client.get_roots() == []

    # Test add_root (no session, so no notification sent)
    result = client.add_root("file:///test/path", "Test Root")
    assert result is True
    roots = client.get_roots()
    assert len(roots) == 1
    assert str(roots[0].uri) == "file:///test/path"
    assert roots[0].name == "Test Root"

    # Test add another root
    result = client.add_root("file:///another/path", "Another Root")
    assert result is True
    roots = client.get_roots()
    assert len(roots) == 2

    # Test adding duplicate root returns False
    result = client.add_root("file:///test/path", "Duplicate Root")
    assert result is False
    roots = client.get_roots()
    assert len(roots) == 2  # Should not have added the duplicate

    # Test remove_root
    removed = client.remove_root("file:///test/path")
    assert removed is True
    roots = client.get_roots()
    assert len(roots) == 1
    assert str(roots[0].uri) == "file:///another/path"

    # Test remove non-existent root
    removed = client.remove_root("file:///nonexistent")
    assert removed is False

    # Test set_roots
    new_roots = [
        types.Root(uri=types.FileUrl("file:///new/path1"), name="New Root 1"),
        types.Root(uri=types.FileUrl("file:///new/path2"), name="New Root 2"),
    ]
    client.set_roots(new_roots)
    roots = client.get_roots()
    assert len(roots) == 2
    assert str(roots[0].uri) == "file:///new/path1"
    assert roots[0].name == "New Root 1"


def test_mcp_roots_adapter_functions():
    """Test MCP roots adapter functions"""
    from gptme.tools.mcp_adapter import add_mcp_root, list_mcp_roots, remove_mcp_root

    # Test list_mcp_roots with no servers loaded
    result = list_mcp_roots()
    assert "No MCP servers loaded" in result

    # Test list_mcp_roots for non-existent server
    result = list_mcp_roots("nonexistent")
    assert "not loaded" in result

    # Test add_mcp_root for non-existent server
    result = add_mcp_root("nonexistent", "file:///test", "Test")
    assert "not loaded" in result

    # Test remove_mcp_root for non-existent server
    result = remove_mcp_root("nonexistent", "file:///test")
    assert "not loaded" in result


def test_mcp_client_event_loop_isolation():
    """Test that creating multiple MCPClient instances doesn't pollute the global event loop.

    Previously, MCPClient.__init__ called asyncio.set_event_loop(self.loop),
    which meant each new client would overwrite the thread-global event loop.
    With multiple MCP servers configured, this caused the last-created client's
    loop to become the global one, potentially breaking async operations on
    earlier clients or other code that relies on the global event loop.
    """
    import asyncio

    from gptme.mcp.client import MCPClient

    # Save whatever the current event loop policy gives us
    original_loop = None
    try:
        original_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        # No current event loop — that's fine
        pass

    # Create multiple clients (simulating multiple MCP servers)
    client_a = MCPClient()
    client_b = MCPClient()
    client_c = MCPClient()

    # Each client should have its own distinct event loop
    assert client_a.loop is not client_b.loop
    assert client_b.loop is not client_c.loop
    assert client_a.loop is not client_c.loop

    # The global event loop should NOT have been changed by creating clients
    try:
        current_loop = asyncio.get_event_loop_policy().get_event_loop()
        if original_loop is not None:
            assert current_loop is original_loop, (
                "MCPClient.__init__ should not change the thread-global event loop"
            )
    except RuntimeError:
        # No event loop set — also acceptable if there wasn't one before
        assert original_loop is None

    # Clean up
    client_a.loop.close()
    client_b.loop.close()
    client_c.loop.close()


def test_mcp_client_close_waits_for_in_flight_call():
    """close() must not run_until_complete while call_tool owns the private loop."""
    import threading
    from unittest.mock import MagicMock

    from gptme.mcp.client import MCPClient

    client = MCPClient()
    client.session = MagicMock()
    order: list[str] = []
    in_run = threading.Event()
    finish_run = threading.Event()

    def fake_run_until_complete(coro):
        order.append("run-start")
        in_run.set()
        assert finish_run.wait(timeout=2)
        order.append("run-end")
        if hasattr(coro, "close"):
            coro.close()
        return "ok"

    client.loop.run_until_complete = fake_run_until_complete  # type: ignore[method-assign]

    call_error: list[BaseException] = []

    def do_call() -> None:
        try:
            client.call_tool("echo", {})
        except BaseException as exc:
            call_error.append(exc)

    caller = threading.Thread(target=do_call)
    caller.start()
    assert in_run.wait(timeout=2)

    closed = threading.Event()

    def do_close() -> None:
        client.close()
        order.append("close")
        closed.set()

    closer = threading.Thread(target=do_close)
    closer.start()
    closer.join(timeout=0.2)
    assert closer.is_alive(), "close() must wait for the in-flight call_tool"
    assert not closed.is_set()

    finish_run.set()
    caller.join(timeout=2)
    closer.join(timeout=2)
    assert not caller.is_alive()
    assert not closer.is_alive()
    assert call_error == []
    assert order == ["run-start", "run-end", "close"]
    assert client.loop.is_closed()

    with pytest.raises(RuntimeError, match="Not connected to MCP server"):
        client.call_tool("echo", {})

    # Reconstruct a session handle so the closed-loop guard is the one that fires.
    client.session = MagicMock()
    with pytest.raises(RuntimeError, match="MCP client is closed"):
        client.call_tool("echo", {})


def test_mcp_client_close_interrupts_stalled_call():
    """close() must not hang if call_tool never returns."""
    import asyncio
    import threading
    import time
    from unittest.mock import MagicMock

    from gptme.mcp.client import MCPClient

    client = MCPClient()
    started = threading.Event()

    async def stall(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("stalled call resumed")

    client.session = MagicMock()
    client.session.call_tool = stall

    call_error: list[BaseException] = []

    def do_call() -> None:
        try:
            client.call_tool("echo", {})
        except BaseException as exc:
            call_error.append(exc)

    caller = threading.Thread(target=do_call)
    caller.start()
    assert started.wait(timeout=2)

    t0 = time.monotonic()
    client.close()
    elapsed = time.monotonic() - t0
    caller.join(timeout=2)

    assert elapsed < 2.0, f"close() hung for {elapsed:.2f}s"
    assert not caller.is_alive()
    assert client.loop.is_closed()
    assert call_error
    assert any("closed" in str(exc).lower() for exc in call_error)


def test_load_mcp_server_registers_toolspecs():
    """load_mcp_server() must register ToolSpecs so dynamically loaded tools are invocable.

    Regression test: previously load_mcp_server() stored the client in _dynamic_servers
    but never called _build_tool_specs_for_server() or invalidated the available-tools cache,
    so the loaded server's tools were silently not invocable.
    """
    from unittest.mock import MagicMock, patch

    import gptme.tools.mcp_adapter as mcp_adapter
    from gptme.config import MCPConfig, MCPServerConfig, UserConfig
    from gptme.tools import _get_available_tools_cache, _set_available_tools_cache
    from gptme.tools.mcp_adapter import _dynamic_servers

    # Build a fake ListToolsResult with one tool
    fake_tool = MagicMock()
    fake_tool.name = "ping"
    fake_tool.description = "A test ping tool"
    fake_tool.inputSchema = {}
    fake_tool.annotations = None

    fake_tools_result = MagicMock()
    fake_tools_result.tools = [fake_tool]

    mock_client = MagicMock()
    mock_client.connect.return_value = (fake_tools_result, MagicMock())
    mock_client.tools = fake_tools_result

    server_cfg = MCPServerConfig(name="testserver", command="echo", enabled=True)
    fake_user_config = UserConfig(mcp=MCPConfig(enabled=True, servers=[server_cfg]))
    fake_config = MagicMock()
    fake_config.mcp = fake_user_config.mcp

    _dynamic_servers.pop("testserver", None)
    _set_available_tools_cache([])  # prime the cache so the append path is exercised

    try:
        with (
            patch.object(mcp_adapter, "get_config", return_value=fake_config),
            patch.object(mcp_adapter, "set_config"),
            patch("gptme.mcp.client.MCPClient", return_value=mock_client),
        ):
            result = mcp_adapter.load_mcp_server("testserver")

        # load must report success
        assert "testserver" in result
        assert "ping" in result

        # client must be in _dynamic_servers
        assert "testserver" in _dynamic_servers

        # ToolSpec must appear in the available-tools cache
        cached = _get_available_tools_cache()
        assert cached is not None, "cache must not be None after load"
        names = [t.name for t in cached]
        assert "testserver.ping" in names, (
            f"testserver.ping missing from cache: {names}"
        )

        # execute function must use _get_mcp_client (clients=None path)
        spec = next(t for t in cached if t.name == "testserver.ping")
        assert spec.execute is not None

        # tool must be in the loaded set (what dispatch / the model tool list use),
        # exactly once even if the load is repeated against a primed cache
        from gptme.tools import _get_loaded_tools, get_tool

        assert get_tool("testserver.ping") is not None
        assert [t.name for t in _get_loaded_tools()].count("testserver.ping") == 1
        assert names.count("testserver.ping") == 1
        # dynamic clients must not leak into the global execution registry
        assert "testserver" not in mcp_adapter._mcp_clients

        # --- unload ---
        with (
            patch.object(mcp_adapter, "get_config", return_value=fake_config),
            patch.object(mcp_adapter, "set_config"),
        ):
            unload_result = mcp_adapter.unload_mcp_server("testserver")

        assert "Successfully unloaded" in unload_result
        assert "testserver" not in _dynamic_servers
        # cache must be cleared so the next get_available_tools() rebuilds without the server
        assert _get_available_tools_cache() is None
        # unloaded tools must stop being invocable in the current context
        assert get_tool("testserver.ping") is None

    finally:
        _dynamic_servers.pop("testserver", None)
        _set_available_tools_cache(None)


def _fake_load_env(server_name: str):
    from unittest.mock import MagicMock

    from gptme.config import MCPConfig, MCPServerConfig

    fake_config = MagicMock()
    fake_config.mcp = MCPConfig(
        enabled=True,
        servers=[MCPServerConfig(name=server_name, command="echo", enabled=True)],
    )
    return fake_config


def test_load_mcp_server_closes_client_when_toolspec_build_fails():
    """A connected client must be closed if a later load step fails."""
    from unittest.mock import MagicMock, patch

    import gptme.tools.mcp_adapter as mcp_adapter
    from gptme.tools.mcp_adapter import _dynamic_servers

    mock_client = MagicMock()
    mock_client.connect.return_value = (MagicMock(), MagicMock())

    _dynamic_servers.pop("failserver", None)
    try:
        with (
            patch.object(
                mcp_adapter, "get_config", return_value=_fake_load_env("failserver")
            ),
            patch.object(mcp_adapter, "set_config"),
            patch("gptme.mcp.client.MCPClient", return_value=mock_client),
            patch.object(
                mcp_adapter,
                "_build_tool_specs_for_server",
                side_effect=ValueError("bad schema"),
            ),
        ):
            result = mcp_adapter.load_mcp_server("failserver")

        assert "Failed to load" in result
        assert "failserver" not in _dynamic_servers
        mock_client.close.assert_called_once()
    finally:
        _dynamic_servers.pop("failserver", None)


def test_load_mcp_server_refuses_name_already_provided_by_session():
    """Loading must not replace tools of a same-named server already in the session
    (e.g. an ACP host-supplied server) with a different server's specs."""
    from unittest.mock import MagicMock, patch

    import gptme.tools.mcp_adapter as mcp_adapter
    from gptme.tools import _get_loaded_tools
    from gptme.tools.base import ToolSpec
    from gptme.tools.mcp_adapter import _dynamic_servers

    session_spec = ToolSpec(name="acpserver.ping", desc="session tool")
    loaded = _get_loaded_tools()
    loaded.append(session_spec)
    mock_client_cls = MagicMock()
    try:
        with (
            patch.object(
                mcp_adapter, "get_config", return_value=_fake_load_env("acpserver")
            ),
            patch.object(mcp_adapter, "set_config"),
            patch("gptme.mcp.client.MCPClient", mock_client_cls),
        ):
            result = mcp_adapter.load_mcp_server("acpserver")

        assert "already loaded" in result
        mock_client_cls.assert_not_called()
        assert "acpserver" not in _dynamic_servers
        assert session_spec in _get_loaded_tools()
    finally:
        loaded[:] = [t for t in loaded if t is not session_spec]
