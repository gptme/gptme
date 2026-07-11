"""
CLI entrypoint for gptme-mcp-server.

Expose gptme tools as an MCP server (stdio transport).
"""

from __future__ import annotations

import logging

import click


@click.command("mcp-server")
@click.option(
    "--tools",
    default=None,
    metavar="TOOLS",
    help="Comma-separated list of tools to expose. "
    "Default: shell,ipython,save,append,read",
)
@click.option(
    "--workspace",
    default=None,
    metavar="DIR",
    help="Working directory for tool execution. Defaults to current directory.",
)
@click.option(
    "--log-level",
    default="WARNING",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Log level for the MCP server process (logs go to stderr).",
)
def main(
    tools: str | None,
    workspace: str | None,
    log_level: str,
) -> None:
    """Expose gptme tools as an MCP server over stdio.

    Connect this server to Claude Desktop, Cursor, or any MCP-compatible client.

    \\b
    Claude Desktop config (~/.claude/claude_desktop_config.json):
        {
          "mcpServers": {
            "gptme": {
              "command": "gptme-mcp-server",
              "args": ["--tools", "shell,ipython,save,read"]
            }
          }
        }
    """
    logging.basicConfig(level=getattr(logging, log_level.upper()), format="%(message)s")

    from ..util.stdio import capture_stdio_transport

    real_stdin, real_stdout = capture_stdio_transport()

    from ..mcp.server import DEFAULT_TOOLS, GptmeMCPServer

    tool_names: list[str] | None = None
    if tools:
        tool_names = [t.strip() for t in tools.split(",") if t.strip()]

    click.echo(
        f"Starting gptme MCP server (tools: {','.join(tool_names or DEFAULT_TOOLS)})",
        err=True,
    )

    server = GptmeMCPServer(tool_names=tool_names, workspace=workspace)
    server.serve_stdio(stdin=real_stdin, stdout=real_stdout)
