"""Tests for the gptme MCP server (gptme/mcp/server.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

pytest.importorskip("mcp", reason="mcp extra not installed")

from gptme.mcp.server import (
    _EXCLUDED_TOOLS,
    DEFAULT_TOOLS,
    GptmeMCPServer,
    _toolspec_to_mcp_tool,
    create_server,
)
from gptme.tools.base import Parameter, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Generator

    from gptme.message import Message


def _noop_execute(
    code: str | None, args: list[str] | None, kwargs: dict[str, str] | None
) -> Generator[Message, None, None]:
    return
    yield  # make it a generator


@pytest.fixture
def simple_tool() -> ToolSpec:
    """A minimal ToolSpec for use in schema tests."""
    return ToolSpec(
        name="shell",
        desc="Executes shell commands.",
        execute=_noop_execute,
        block_types=["shell"],
        parameters=[
            Parameter(
                name="command",
                type="string",
                description="The shell command to execute.",
                required=True,
            ),
        ],
    )


@pytest.fixture
def multi_param_tool() -> ToolSpec:
    """A ToolSpec with multiple parameters to test schema mapping."""
    return ToolSpec(
        name="read",
        desc="Read files.",
        execute=_noop_execute,
        block_types=["read"],
        parameters=[
            Parameter(
                name="path", type="string", description="File path.", required=True
            ),
            Parameter(
                name="start_line",
                type="integer",
                description="Start line.",
                required=False,
            ),
            Parameter(
                name="end_line", type="integer", description="End line.", required=False
            ),
        ],
    )


class TestToolspecToMcpTool:
    """Tests for _toolspec_to_mcp_tool schema conversion."""

    def test_basic_conversion(self, simple_tool: ToolSpec) -> None:
        mcp_tool = _toolspec_to_mcp_tool(simple_tool)
        assert mcp_tool.name == "shell"
        assert mcp_tool.description == "Executes shell commands."

    def test_required_param_in_schema(self, simple_tool: ToolSpec) -> None:
        mcp_tool = _toolspec_to_mcp_tool(simple_tool)
        schema = mcp_tool.inputSchema
        assert schema["type"] == "object"
        assert "command" in schema["properties"]
        assert schema["properties"]["command"]["type"] == "string"
        assert "command" in schema["required"]

    def test_optional_params_not_in_required(self, multi_param_tool: ToolSpec) -> None:
        mcp_tool = _toolspec_to_mcp_tool(multi_param_tool)
        schema = mcp_tool.inputSchema
        assert "path" in schema["required"]
        assert "start_line" not in schema["required"]
        assert "end_line" not in schema["required"]

    def test_integer_type_mapping(self, multi_param_tool: ToolSpec) -> None:
        mcp_tool = _toolspec_to_mcp_tool(multi_param_tool)
        schema = mcp_tool.inputSchema
        assert schema["properties"]["start_line"]["type"] == "integer"
        assert schema["properties"]["end_line"]["type"] == "integer"

    def test_empty_parameters(self) -> None:
        tool = ToolSpec(
            name="noparams",
            desc="No parameters.",
            execute=_noop_execute,
            block_types=["noparams"],
            parameters=[],
        )
        mcp_tool = _toolspec_to_mcp_tool(tool)
        schema = mcp_tool.inputSchema
        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_description_in_property(self, simple_tool: ToolSpec) -> None:
        mcp_tool = _toolspec_to_mcp_tool(simple_tool)
        prop = mcp_tool.inputSchema["properties"]["command"]
        assert prop.get("description") == "The shell command to execute."

    def test_enum_param(self) -> None:
        tool = ToolSpec(
            name="enumtool",
            desc="Enum tool.",
            execute=_noop_execute,
            block_types=["enumtool"],
            parameters=[
                Parameter(
                    name="mode",
                    type="string",
                    description="Mode.",
                    enum=["fast", "slow"],
                    required=True,
                ),
            ],
        )
        mcp_tool = _toolspec_to_mcp_tool(tool)
        prop = mcp_tool.inputSchema["properties"]["mode"]
        assert prop["enum"] == ["fast", "slow"]


class TestGptmeMCPServer:
    """Tests for GptmeMCPServer construction and tool filtering."""

    def test_default_tools(self) -> None:
        server = GptmeMCPServer()
        assert server._tool_names == DEFAULT_TOOLS

    def test_excluded_tools_filtered_out(self) -> None:
        tool_names = ["shell", "subagent", "mcp", "ipython"]
        server = GptmeMCPServer(tool_names=tool_names)
        for excluded in _EXCLUDED_TOOLS:
            assert excluded not in server._tool_names

    def test_custom_tools(self) -> None:
        server = GptmeMCPServer(tool_names=["shell", "read"])
        assert server._tool_names == ["shell", "read"]

    def test_workspace_stored(self) -> None:
        server = GptmeMCPServer(workspace="/tmp/test")
        assert server._workspace == "/tmp/test"

    def test_create_server_factory(self) -> None:
        server = create_server(tool_names=["shell"])
        assert isinstance(server, GptmeMCPServer)
        assert server._tool_names == ["shell"]


class TestMCPServerHandlers:
    """Tests for the MCP request handlers using mock tools."""

    @pytest.fixture
    def server_with_mock_tools(self, simple_tool: ToolSpec) -> GptmeMCPServer:
        server = GptmeMCPServer(tool_names=["shell"])
        server._loaded_tools = [simple_tool]
        return server

    @pytest.mark.asyncio
    async def test_list_tools_returns_loaded(
        self, server_with_mock_tools: GptmeMCPServer
    ) -> None:
        """list_tools handler returns tools that have an execute function."""
        # Access the registered handler directly via server's request_handlers
        import mcp.types as types

        req = types.ListToolsRequest(method="tools/list", params=None)
        result = await server_with_mock_tools._server.request_handlers[
            types.ListToolsRequest
        ](req)
        assert result.root.tools  # type: ignore[union-attr]
        tool_names = [t.name for t in result.root.tools]  # type: ignore[union-attr]
        assert "shell" in tool_names

    @pytest.mark.asyncio
    async def test_list_tools_excludes_no_execute(self) -> None:
        """Tools without an execute function are not listed."""
        import mcp.types as types

        no_execute_tool = ToolSpec(
            name="noexec",
            desc="No execute.",
            parameters=[],
        )
        server = GptmeMCPServer(tool_names=["noexec"])
        server._loaded_tools = [no_execute_tool]

        req = types.ListToolsRequest(method="tools/list", params=None)
        result = await server._server.request_handlers[types.ListToolsRequest](req)
        tool_names = [t.name for t in result.root.tools]  # type: ignore[union-attr]
        assert "noexec" not in tool_names

    @pytest.mark.asyncio
    async def test_call_tool_unknown_returns_error(
        self, server_with_mock_tools: GptmeMCPServer
    ) -> None:
        """call_tool for an unknown tool name returns an error result."""
        import mcp.types as types

        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(name="nonexistent", arguments={}),
        )
        result = await server_with_mock_tools._server.request_handlers[
            types.CallToolRequest
        ](req)
        # MCP wraps handler exceptions into an isError=True CallToolResult
        assert result.root.isError is True  # type: ignore[union-attr]
        assert any(
            "nonexistent" in c.text
            for c in result.root.content  # type: ignore[union-attr]
            if hasattr(c, "text")
        )

    @pytest.mark.asyncio
    async def test_call_tool_collects_output(
        self, server_with_mock_tools: GptmeMCPServer
    ) -> None:
        """call_tool executes the tool and returns text output."""
        import mcp.types as types

        from gptme.message import Message

        def fake_execute(code, args, kwargs):
            yield Message("system", "hello from tool")

        server_with_mock_tools._loaded_tools[0] = ToolSpec(
            name="shell",
            desc="Shell.",
            execute=fake_execute,
            block_types=["shell"],
            parameters=[
                Parameter(name="command", type="string", required=True),
            ],
        )

        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="shell", arguments={"command": "echo hi"}
            ),
        )

        result = await server_with_mock_tools._server.request_handlers[
            types.CallToolRequest
        ](req)

        content = result.root.content  # type: ignore[union-attr]
        assert any("hello from tool" in c.text for c in content if hasattr(c, "text"))

    @pytest.mark.asyncio
    async def test_shell_session_reused_across_calls(
        self, server_with_mock_tools: GptmeMCPServer
    ) -> None:
        """The same ShellSession must be injected into every executor thread.

        run_in_executor copies the async context via copy_context(), so _shell_var
        resets to None in each thread without explicit injection. This test verifies
        that the server pre-seeds _shell_var with its persistent session so stateful
        tools (shell, ipython) see the same subprocess on every call.
        """
        from unittest.mock import MagicMock

        import mcp.types as types

        from gptme.message import Message
        from gptme.tools.shell import _shell_var

        # Use a MagicMock so we avoid spawning a real bash subprocess in tests.
        mock_session = MagicMock()
        server_with_mock_tools._shell_session = mock_session

        captured_sessions: list[object] = []

        def session_spy(code, args, kwargs):
            captured_sessions.append(_shell_var.get())
            yield Message("system", "ok")

        server_with_mock_tools._loaded_tools[0] = ToolSpec(
            name="shell",
            desc="Shell.",
            execute=session_spy,
            block_types=["shell"],
            parameters=[Parameter(name="command", type="string", required=True)],
        )

        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="shell", arguments={"command": "echo test"}
            ),
        )
        handlers = server_with_mock_tools._server.request_handlers
        await handlers[types.CallToolRequest](req)
        await handlers[types.CallToolRequest](req)

        assert len(captured_sessions) == 2, "spy must run twice"
        assert captured_sessions[0] is mock_session, (
            "First call must see the persistent session"
        )
        assert captured_sessions[1] is mock_session, (
            "Second call must reuse the same session"
        )


class TestMCPServerCLI:
    """Tests for the gptme-mcp-server CLI command."""

    def test_cli_help(self) -> None:
        from click.testing import CliRunner

        from gptme.cli.cmd_mcp_serve import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "gptme tools" in result.output or "MCP" in result.output

    def test_cli_invalid_log_level(self) -> None:
        from click.testing import CliRunner

        from gptme.cli.cmd_mcp_serve import main

        runner = CliRunner()
        result = runner.invoke(main, ["--log-level", "INVALID"])
        assert result.exit_code != 0


class TestWorkspaceHandling:
    """Tests for --workspace CWD behaviour."""

    def test_serve_stdio_changes_cwd_to_workspace(self, tmp_path) -> None:
        """serve_stdio must os.chdir to the workspace so file tools use the right dir."""
        from unittest.mock import patch

        server = GptmeMCPServer(workspace=str(tmp_path))

        chdir_calls: list[str] = []

        # Close the coroutine passed to asyncio.run to avoid "was never awaited" warning.
        def _drain_coro(coro):
            coro.close()

        with (
            patch("gptme.mcp.server.os.chdir", side_effect=chdir_calls.append),
            patch.object(server, "_init_tools"),
            patch("asyncio.run", side_effect=_drain_coro),
        ):
            server.serve_stdio()

        assert chdir_calls == [str(tmp_path)], (
            f"Expected os.chdir({tmp_path!s}) but got {chdir_calls}"
        )

    def test_serve_stdio_no_chdir_without_workspace(self) -> None:
        """serve_stdio must not call os.chdir when workspace is None."""
        from unittest.mock import patch

        server = GptmeMCPServer(workspace=None)

        def _drain_coro(coro):
            coro.close()

        with (
            patch("gptme.mcp.server.os.chdir") as mock_chdir,
            patch.object(server, "_init_tools"),
            patch("asyncio.run", side_effect=_drain_coro),
        ):
            server.serve_stdio()

        mock_chdir.assert_not_called()


class TestToolCallSerialization:
    """Tests for per-call asyncio.Lock preventing concurrent stateful access."""

    def test_server_has_tool_call_lock(self) -> None:
        """GptmeMCPServer must expose a _tool_call_lock for serializing calls."""
        import asyncio

        server = GptmeMCPServer()
        assert isinstance(server._tool_call_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_concurrent_calls_are_serialized(self, simple_tool: ToolSpec) -> None:
        """Two overlapping MCP tool calls must not interleave inside the lock."""
        import asyncio

        import mcp.types as types

        from gptme.message import Message

        order: list[str] = []

        def sync_execute(code, args, kwargs):
            order.append("start")
            # Yield to the event loop once; a non-serializing server would let
            # a second call start here before this one appends "end".
            order.append("end")
            yield Message("system", "done")

        server = GptmeMCPServer(tool_names=["shell"])
        server._loaded_tools = [
            ToolSpec(
                name="shell",
                desc="Shell.",
                execute=sync_execute,
                block_types=["shell"],
                parameters=[Parameter(name="command", type="string", required=True)],
            )
        ]

        req = types.CallToolRequest(
            method="tools/call",
            params=types.CallToolRequestParams(
                name="shell", arguments={"command": "echo test"}
            ),
        )
        handler = server._server.request_handlers[types.CallToolRequest]
        await asyncio.gather(handler(req), handler(req))

        # Serialized order must be start,end,start,end — never start,start,...
        assert order == ["start", "end", "start", "end"], (
            f"Tool calls interleaved: {order}"
        )


class TestLazyServerImport:
    """Tests for P2: client imports must not trigger server module loading."""

    def test_mcp_client_import_does_not_load_server_module(self) -> None:
        """from gptme.mcp import MCPClient must not import gptme.mcp.server."""
        import sys

        # Remove the server module from the cache (if already loaded by this test run).
        for key in list(sys.modules):
            if key == "gptme.mcp.server" or key.startswith("gptme.mcp.server."):
                del sys.modules[key]

        # Importing MCPClient should NOT cause server.py to be loaded.
        from gptme.mcp import MCPClient  # noqa: F401

        assert "gptme.mcp.server" not in sys.modules, (
            "Importing MCPClient triggered gptme.mcp.server import — "
            "this loads the full tools package as a side-effect."
        )

    def test_lazy_getattr_loads_server_on_demand(self) -> None:
        """Accessing GptmeMCPServer via gptme.mcp triggers the lazy import."""
        import sys

        # Ensure server module is not in cache.
        for key in list(sys.modules):
            if key == "gptme.mcp.server" or key.startswith("gptme.mcp.server."):
                del sys.modules[key]

        import gptme.mcp as mcp_pkg

        _ = mcp_pkg.GptmeMCPServer  # trigger __getattr__

        assert "gptme.mcp.server" in sys.modules, (
            "Accessing GptmeMCPServer should load gptme.mcp.server lazily."
        )
