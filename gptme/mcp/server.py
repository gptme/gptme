"""
MCP server: expose gptme tools as an MCP server.

Allows Claude Desktop, Cursor, and other MCP clients to use gptme's tools
(bash, Python REPL, file read/save, browser, etc.) directly.

Usage (stdio transport, recommended for Claude Desktop):
    gptme-mcp-server

Claude Desktop config:
    {
      "mcpServers": {
        "gptme": {
          "command": "gptme-mcp-server",
          "args": ["--tools", "shell,ipython,save,append,read"]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import Server

from ..tools import init_tools

if TYPE_CHECKING:
    from collections.abc import Generator

    from ..tools.base import ToolSpec
    from ..tools.shell import ShellSession

logger = logging.getLogger(__name__)

# Default tool set: broadly useful, excluding circular (mcp) and risky (subagent).
DEFAULT_TOOLS = ["shell", "ipython", "save", "append", "read"]

# Tools that should never be exposed via MCP regardless of user request.
_EXCLUDED_TOOLS = {"subagent", "mcp"}

_PARAM_TYPE_MAP: dict[str, str] = {
    "string": "string",
    "str": "string",
    "integer": "integer",
    "int": "integer",
    "number": "number",
    "float": "number",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "object": "object",
}


def _param_type_to_json(type_str: str) -> str:
    """Convert a gptme parameter type string to a JSON Schema type."""
    return _PARAM_TYPE_MAP.get(type_str.lower(), "string")


def _toolspec_to_mcp_tool(tool: ToolSpec) -> types.Tool:
    """Convert a gptme ToolSpec to an MCP Tool with JSON Schema input schema."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in tool.parameters:
        prop: dict[str, Any] = {"type": _param_type_to_json(param.type)}
        if param.description:
            prop["description"] = param.description
        if param.enum:
            prop["enum"] = param.enum
        properties[param.name] = prop
        if param.required:
            required.append(param.name)

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        input_schema["required"] = required

    return types.Tool(
        name=tool.name,
        description=tool.desc,
        inputSchema=input_schema,
    )


def _collect_tool_output(gen: Generator[Any, None, None]) -> str:
    """Collect text output from a tool execute() generator."""
    return "\n".join(msg.content for msg in gen if msg.content)


class GptmeMCPServer:
    """Session-backed MCP server that exposes gptme tools to MCP clients.

    One server instance = one persistent gptme session, so bash retains shell
    state, the Python REPL persists variables, etc. across multiple tool calls.
    """

    def __init__(
        self,
        tool_names: list[str] | None = None,
        workspace: str | None = None,
    ) -> None:
        self._tool_names = [
            t for t in (tool_names or DEFAULT_TOOLS) if t not in _EXCLUDED_TOOLS
        ]
        self._workspace = workspace
        self._server = Server("gptme")
        self._loaded_tools: list[ToolSpec] = []
        # Persistent shell session shared across all tool calls. Lazily created
        # on first use; injected into each executor thread via _shell_var so that
        # stateful tools (shell, ipython) retain state between MCP requests.
        self._shell_session: ShellSession | None = None
        self._setup_handlers()

    def _get_or_create_shell_session(self) -> ShellSession:
        """Lazily create and return the server's persistent shell session.

        Called from inside executor threads; the returned session is then injected
        into the thread's ContextVar copy so that subsequent get_shell() calls in
        that thread return the same subprocess instead of spawning a fresh one.
        """
        if self._shell_session is None:
            from ..tools.shell import ShellSession

            self._shell_session = ShellSession(cwd=self._workspace)
        return self._shell_session

    def _setup_handlers(self) -> None:
        server = self._server

        @server.list_tools()
        async def handle_list_tools() -> list[types.Tool]:
            return [
                _toolspec_to_mcp_tool(t)
                for t in self._loaded_tools
                if t.execute is not None
            ]

        @server.call_tool()
        async def handle_call_tool(
            name: str, arguments: dict
        ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
            tool = next((t for t in self._loaded_tools if t.name == name), None)
            if tool is None:
                raise ValueError(f"Tool '{name}' not found in loaded tools")
            if tool.execute is None:
                raise ValueError(f"Tool '{name}' has no execute function")

            # All arguments arrive as kwargs; each tool's execute() checks kwargs
            # first (or alongside code/args) so this mapping is universal.
            kwargs = {k: str(v) for k, v in arguments.items()} if arguments else {}

            # Call tool.execute() directly with kwargs — avoids thread-local registry
            # lookup in ToolUse.execute(). Auto-confirm is handled by the registered
            # hook (register_auto_confirm called in _init_tools).
            def _run_tool() -> str:
                # run_in_executor copies the async context via copy_context(), so
                # _shell_var resets to None in each new thread — every call would
                # spawn a fresh subprocess, breaking the advertised shell persistence.
                # Pre-seed the ContextVar with the server's persistent session so
                # get_shell() reuses it instead of creating a new one.
                from ..tools.shell import _shell_var as _shell_ctxvar

                _shell_ctxvar.set(self._get_or_create_shell_session())

                result = tool.execute(None, None, kwargs)  # type: ignore[misc]
                if hasattr(result, "__iter__"):
                    output = _collect_tool_output(result)  # type: ignore[arg-type]
                else:
                    output = str(result.content) if result and result.content else ""

                # Sync back any session replaced during execution (e.g. by set_shell).
                updated = _shell_ctxvar.get()
                if updated is not None:
                    self._shell_session = updated

                return output

            # Run in a thread executor to avoid blocking the event loop during
            # long-running operations (bash commands, Python REPL, etc.)
            loop = asyncio.get_event_loop()
            output = await loop.run_in_executor(None, _run_tool)

            return [types.TextContent(type="text", text=output or "(no output)")]

    def _init_tools(self) -> None:
        """Initialize gptme tools and register auto-confirm for non-interactive use."""
        from ..hooks.auto_confirm import register as register_auto_confirm

        register_auto_confirm()
        self._loaded_tools = init_tools(self._tool_names)
        logger.info(
            "Loaded tools: %s", [t.name for t in self._loaded_tools if t.execute]
        )

    def serve_stdio(self) -> None:
        """Run the MCP server over stdio (for Claude Desktop and similar clients)."""
        self._init_tools()

        async def _run() -> None:
            async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
                await self._server.run(
                    read_stream,
                    write_stream,
                    self._server.create_initialization_options(),
                )

        asyncio.run(_run())


def create_server(
    tool_names: list[str] | None = None,
    workspace: str | None = None,
) -> GptmeMCPServer:
    """Create and return a GptmeMCPServer instance."""
    return GptmeMCPServer(tool_names=tool_names, workspace=workspace)
