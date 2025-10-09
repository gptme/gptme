"""
MCP Registry and Discovery

This module provides functionality to search and discover MCP servers from various registries.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class MCPServerInfo:
    """Information about an MCP server from a registry."""

    name: str
    description: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    registry: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    repository: str = ""
    version: str = ""
    install_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "command": self.command,
            "args": self.args,
            "url": self.url,
            "registry": self.registry,
            "tags": self.tags,
            "author": self.author,
            "repository": self.repository,
            "version": self.version,
            "install_command": self.install_command,
        }


class MCPRegistry:
    """Interface to search and discover MCP servers from registries."""

    OFFICIAL_REGISTRY_URL = "https://registry.modelcontextprotocol.io"
    MCP_SO_API_URL = "https://mcp.so/api/servers"

    def search_official_registry(
        self, query: str = "", limit: int = 10
    ) -> list[MCPServerInfo]:
        """
        Search the official MCP Registry.

        Args:
            query: Search query (searches name, description, tags)
            limit: Maximum number of results

        Returns:
            List of MCPServerInfo objects
        """
        try:
            # The official registry API might have a search endpoint
            # This is a placeholder - adjust based on actual API
            url = f"{self.OFFICIAL_REGISTRY_URL}/api/search"
            params = {"q": query, "limit": limit} if query else {"limit": limit}

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            results = []
            for item in response.json().get("servers", []):
                results.append(
                    MCPServerInfo(
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        command=item.get("command", ""),
                        args=item.get("args", []),
                        url=item.get("url", ""),
                        registry="official",
                        tags=item.get("tags", []),
                        author=item.get("author", ""),
                        repository=item.get("repository", ""),
                        version=item.get("version", ""),
                        install_command=item.get("install_command", ""),
                    )
                )
            return results
        except requests.RequestException as e:
            logger.warning(f"Failed to search official registry: {e}")
            return []

    def search_mcp_so(self, query: str = "", limit: int = 10) -> list[MCPServerInfo]:
        """
        Search MCP.so directory.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of MCPServerInfo objects
        """
        try:
            # MCP.so might have an API endpoint for search
            # This is a placeholder - adjust based on actual API
            response = requests.get(self.MCP_SO_API_URL, timeout=10)
            response.raise_for_status()

            results = []
            servers = response.json().get("servers", [])

            # Filter by query if provided
            if query:
                query_lower = query.lower()
                servers = [
                    s
                    for s in servers
                    if query_lower in s.get("name", "").lower()
                    or query_lower in s.get("description", "").lower()
                    or any(query_lower in tag.lower() for tag in s.get("tags", []))
                ]

            for item in servers[:limit]:
                results.append(
                    MCPServerInfo(
                        name=item.get("name", ""),
                        description=item.get("description", ""),
                        command=item.get("command", ""),
                        args=item.get("args", []),
                        url=item.get("url", ""),
                        registry="mcp.so",
                        tags=item.get("tags", []),
                        author=item.get("author", ""),
                        repository=item.get("repository", ""),
                        version=item.get("version", ""),
                        install_command=item.get("install_command", ""),
                    )
                )
            return results
        except requests.RequestException as e:
            logger.warning(f"Failed to search MCP.so: {e}")
            return []

    def search_all(self, query: str = "", limit: int = 10) -> list[MCPServerInfo]:
        """
        Search all available registries.

        Args:
            query: Search query
            limit: Maximum number of results per registry

        Returns:
            Combined list of MCPServerInfo objects from all registries
        """
        results = []
        results.extend(self.search_official_registry(query, limit))
        results.extend(self.search_mcp_so(query, limit))
        return results

    def get_server_details(self, name: str) -> MCPServerInfo | None:
        """
        Get detailed information about a specific server.

        Args:
            name: Server name

        Returns:
            MCPServerInfo object or None if not found
        """
        # Search all registries for the specific server
        results = self.search_all(name, limit=50)
        for server in results:
            if server.name == name:
                return server
        return None


def format_server_list(servers: list[MCPServerInfo]) -> str:
    """
    Format a list of servers for display.

    Args:
        servers: List of MCPServerInfo objects

    Returns:
        Formatted string
    """
    if not servers:
        return "No servers found."

    output = []
    for i, server in enumerate(servers, 1):
        output.append(f"{i}. **{server.name}** ({server.registry})")
        output.append(f"   {server.description}")
        if server.tags:
            output.append(f"   Tags: {', '.join(server.tags)}")
        if server.repository:
            output.append(f"   Repository: {server.repository}")
        if server.install_command:
            output.append(f"   Install: `{server.install_command}`")
        output.append("")

    return "\n".join(output)


def format_server_details(server: MCPServerInfo) -> str:
    """
    Format detailed server information for display.

    Args:
        server: MCPServerInfo object

    Returns:
        Formatted string
    """
    output = [
        f"# {server.name}",
        "",
        f"**Description:** {server.description}",
        "",
    ]

    if server.registry:
        output.append(f"**Registry:** {server.registry}")
    if server.author:
        output.append(f"**Author:** {server.author}")
    if server.version:
        output.append(f"**Version:** {server.version}")
    if server.repository:
        output.append(f"**Repository:** {server.repository}")

    output.append("")

    if server.tags:
        output.append(f"**Tags:** {', '.join(server.tags)}")
        output.append("")

    if server.install_command:
        output.append("## Installation")
        output.append("")
        output.append(f"```bash\n{server.install_command}\n```")
        output.append("")

    if server.command:
        output.append("## Configuration")
        output.append("")
        output.append("```toml")
        output.append("[[mcp.servers]]")
        output.append(f'name = "{server.name}"')
        output.append("enabled = true")
        output.append(f'command = "{server.command}"')
        if server.args:
            output.append(f"args = {json.dumps(server.args)}")
        output.append("```")
    elif server.url:
        output.append("## Configuration")
        output.append("")
        output.append("```toml")
        output.append("[[mcp.servers]]")
        output.append(f'name = "{server.name}"')
        output.append("enabled = true")
        output.append(f'url = "{server.url}"')
        output.append("```")

    return "\n".join(output)
