"""CLI commands for MCP (Model Context Protocol) server management."""

import click

from ..config import get_config
from ..mcp.client import MCPClient


@click.group()
def mcp():
    """Commands for managing MCP servers."""


@mcp.command("list")
def mcp_list():
    """List MCP servers and check their connection health."""

    config = get_config()

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

    config = get_config()

    if not config.mcp.enabled:
        click.echo("❌ MCP is disabled in config")
        return

    server = next((s for s in config.mcp.servers if s.name == server_name), None)
    if not server:
        click.echo(f"❌ Server '{server_name}' not found in config")
        return

    if not server.enabled:
        click.echo(f"❌ Server '{server_name}' is disabled")
        return

    server_type = "HTTP" if server.is_http else "stdio"
    click.echo(f"🔌 Testing {server_name} ({server_type})...")

    try:
        client = MCPClient(config)
        tools, session = client.connect(server_name)
        click.echo("✅ Connected successfully!")
        click.echo(f"📋 Available tools ({len(tools.tools)}):")

        for tool in tools.tools:
            click.echo(f"   • {tool.name}: {tool.description or 'No description'}")

    except Exception as e:
        click.echo(f"❌ Connection failed: {e}")


@mcp.command("info")
@click.argument("server_name")
def mcp_info(server_name: str):
    """Show detailed information about an MCP server.

    Checks configured servers first, then searches registries if not found locally.
    """
    from ..mcp.registry import MCPRegistry, format_server_details

    config = get_config()

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
        except Exception as e:
            click.echo(f"❌ Registry search failed: {e}")


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
            return

        if results:
            click.echo(format_server_list(results))
        else:
            click.echo("No servers found.")
    except Exception as e:
        click.echo(f"❌ Search failed: {e}")
