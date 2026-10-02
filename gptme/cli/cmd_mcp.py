"""CLI commands for MCP (Model Context Protocol) server management."""

import shlex
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import click

from ..config import Config, MCPServerConfig
from ..mcp.client import MCPClient


@click.group()
def mcp():
    """Commands for managing MCP servers."""


_REDACTED_VALUE = "***"


def _redact_param_values(params: str) -> str:
    """Mask every ``name=value`` value in a query or fragment string.

    Values are masked regardless of the parameter name: a credential can ride
    under any name (``?sig=``, ``?X-Amz-Signature=``), so redacting only a
    hardcoded set of "known" credential names would leave the rest exposed.
    Names are kept so the target stays identifiable, and a malformed pair is
    passed through verbatim.
    """
    if not params:
        return params
    redacted: list[str] = []
    for pair in params.split("&"):
        name, sep, _ = pair.partition("=")
        redacted.append(f"{name}={_REDACTED_VALUE}" if sep else pair)
    return "&".join(redacted)


def _display_target(server: MCPServerConfig) -> str:
    """Render a server target for confirmation, hiding URL credentials.

    Redacts userinfo (``user:pass@``) and every query/fragment parameter value.
    Never raises: a malformed port or an IPv6 host must not abort the
    diagnostic before the approval prompt is even shown.
    """
    if server.is_http:
        parts = urlsplit(server.url or "")
        if parts.username or parts.password:
            # Strip only the userinfo; keep host:port verbatim so an invalid
            # port or IPv6 brackets still match the URL the client will use.
            parts = parts._replace(netloc=parts.netloc.rpartition("@")[2])
        # A credential can ride in any query/fragment parameter, not just the
        # conventional names, so mask every value and keep only the names.
        if parts.query:
            parts = parts._replace(query=_redact_param_values(parts.query))
        if parts.fragment:
            parts = parts._replace(fragment=_redact_param_values(parts.fragment))
        return urlunsplit(parts)
    return shlex.join([server.command or "", *server.args])


def _confirm_project_connection(config: Config, server: MCPServerConfig) -> bool:
    """Require consent before connecting to a workspace-supplied server.

    The target is shown so the user can inspect what will run. URL userinfo and
    query/fragment parameter values are redacted; command arguments are shown
    verbatim because they define the command being approved.
    """
    if not config.project or not config.project.mcp:
        return True
    if server.name not in {s.name for s in config.project.mcp.servers}:
        return True
    target = _display_target(server)
    try:
        return click.confirm(
            f"Connect to project MCP server {server.name!r} ({target!r})?",
            default=False,
        )
    except click.Abort:
        return False


@mcp.command("list")
def mcp_list():
    """List MCP servers and check their connection health."""

    config = Config.from_workspace(Path.cwd())

    if not config.mcp.enabled:
        click.echo("❌ MCP is disabled in config")
        return

    if not config.mcp.servers:
        click.echo("📭 No MCP servers configured")
        return

    click.echo(f"🔌 Found {len(config.mcp.servers)} MCP server(s):")
    click.echo()

    for server in config.mcp.servers:
        status_icon = "🟢" if server.enabled else "🔴"
        server_type = "HTTP" if server.is_http else "stdio"

        click.echo(f"{status_icon} {server.name} ({server_type})")

        if not server.enabled:
            click.echo("   Status: Disabled")
            click.echo()
            continue

        if not _confirm_project_connection(config, server):
            click.echo("   Status: Connection skipped (not approved)")
            click.echo()
            continue

        # Test connection
        try:
            client = MCPClient(config)
            tools, session = client.connect(server.name)
            click.echo(f"   Status: ✅ Connected ({len(tools.tools)} tools available)")

            # Show first few tools
            if tools.tools:
                tool_names = [tool.name for tool in tools.tools[:3]]
                more = (
                    f" (+{len(tools.tools) - 3} more)" if len(tools.tools) > 3 else ""
                )
                click.echo(f"   Tools: {', '.join(tool_names)}{more}")
        except Exception as e:
            click.echo(f"   Status: ❌ Connection failed: {e}")

        click.echo()


@mcp.command("test")
@click.argument("server_name")
def mcp_test(server_name: str):
    """Test connection to a specific MCP server."""

    config = Config.from_workspace(Path.cwd())

    if not config.mcp.enabled:
        click.echo("❌ MCP is disabled in config")
        sys.exit(1)

    server = next((s for s in config.mcp.servers if s.name == server_name), None)
    if not server:
        click.echo(f"❌ Server '{server_name}' not found in config")
        sys.exit(1)

    if not server.enabled:
        click.echo(f"❌ Server '{server_name}' is disabled")
        sys.exit(1)

    server_type = "HTTP" if server.is_http else "stdio"
    click.echo(f"🔌 Testing {server_name} ({server_type})...")
    if not _confirm_project_connection(config, server):
        raise click.ClickException("Project server connection was not approved")

    try:
        client = MCPClient(config)
        tools, session = client.connect(server_name)
        click.echo("✅ Connected successfully!")
        click.echo(f"📋 Available tools ({len(tools.tools)}):")

        for tool in tools.tools:
            click.echo(f"   • {tool.name}: {tool.description or 'No description'}")

    except Exception as e:
        click.echo(f"❌ Connection failed: {e}")
        sys.exit(1)


@mcp.command("info")
@click.argument("server_name")
def mcp_info(server_name: str):
    """Show detailed information about an MCP server.

    Checks configured servers first, then searches registries if not found locally.
    """
    from ..mcp.registry import MCPRegistry, format_server_details

    config = Config.from_workspace(Path.cwd())

    # First check if server is configured locally
    server = next((s for s in config.mcp.servers if s.name == server_name), None)

    if server:
        # Show local configuration
        click.echo(f"📋 MCP Server: {server.name}")
        click.echo(f"   Type: {'HTTP' if server.is_http else 'stdio'}")
        click.echo(f"   Enabled: {'✅' if server.enabled else '❌'}")
        click.echo()

        if server.is_http:
            click.echo(f"   URL: {server.url}")
            if server.headers:
                click.echo(f"   Headers: {len(server.headers)} configured")
        else:
            click.echo(f"   Command: {server.command}")
            if server.args:
                click.echo(f"   Args: {' '.join(server.args)}")
            if server.env:
                click.echo(f"   Environment: {len(server.env)} variables")

        # Try to test connection if enabled
        if server.enabled:
            click.echo()
            if not _confirm_project_connection(config, server):
                click.echo("Connection skipped (not approved).")
                return
            click.echo("Testing connection...")
            try:
                client = MCPClient(config)
                tools, session = client.connect(server_name)
                click.echo(f"✅ Connected ({len(tools.tools)} tools available)")
            except Exception as e:
                click.echo(f"❌ Connection failed: {e}")
    else:
        # Not found locally, search registries
        click.echo(f"Server '{server_name}' not configured locally.")
        click.echo("🔍 Searching registries...")
        click.echo()

        reg = MCPRegistry()
        try:
            registry_server = reg.get_server_details(server_name)
            if registry_server:
                click.echo(format_server_details(registry_server))
            else:
                click.echo(f"❌ Server '{server_name}' not found in registries either.")
                click.echo("\nTry searching: gptme-util mcp search <query>")
                sys.exit(1)
        except Exception as e:
            click.echo(f"❌ Registry search failed: {e}")
            sys.exit(1)


@mcp.command("serve")
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
def mcp_serve(tools: str | None, workspace: str | None, log_level: str) -> None:
    """Expose gptme tools as an MCP server over stdio.

    Connect this server to Claude Desktop, Cursor, or any MCP-compatible client.

    \b
    Claude Desktop config (~/.claude/claude_desktop_config.json):
        {
          "mcpServers": {
            "gptme": {
              "command": "gptme-util",
              "args": ["mcp", "serve", "--tools", "shell,ipython,save,read"]
            }
          }
        }

    Tip: the standalone ``gptme-mcp-server`` command is equivalent and slightly
    shorter for Claude Desktop configs.
    """
    import logging

    from ..mcp.server import DEFAULT_TOOLS, GptmeMCPServer

    logging.basicConfig(level=getattr(logging, log_level.upper()), format="%(message)s")

    tool_names: list[str] | None = None
    if tools:
        tool_names = [t.strip() for t in tools.split(",") if t.strip()]

    click.echo(
        f"Starting gptme MCP server (tools: {','.join(tool_names or DEFAULT_TOOLS)})",
        err=True,
    )

    server = GptmeMCPServer(tool_names=tool_names, workspace=workspace)
    server.serve_stdio()


@mcp.command("search")
@click.argument("query", required=False, default="")
@click.option(
    "-r",
    "--registry",
    default="all",
    type=click.Choice(["all", "official", "mcp.so"]),
    help="Registry to search",
)
@click.option("-n", "--limit", default=10, help="Maximum number of results")
def mcp_search(query: str, registry: str, limit: int):
    """Search for MCP servers in registries."""
    from ..mcp.registry import MCPRegistry, format_server_list

    if registry == "all":
        click.echo(f"🔍 Searching all registries for '{query}'...")
    else:
        click.echo(f"🔍 Searching {registry} registry for '{query}'...")
    click.echo()

    reg = MCPRegistry()

    try:
        if registry == "all":
            results = reg.search_all(query, limit)
        elif registry == "official":
            results = reg.search_official_registry(query, limit)
        elif registry == "mcp.so":
            results = reg.search_mcp_so(query, limit)
        else:
            click.echo(f"❌ Unknown registry: {registry}")
            sys.exit(1)

        if results:
            click.echo(format_server_list(results))
        else:
            click.echo("No servers found.")
    except Exception as e:
        click.echo(f"❌ Search failed: {e}")
        sys.exit(1)
