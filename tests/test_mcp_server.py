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
