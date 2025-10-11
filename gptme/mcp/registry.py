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
    OFFICIAL_REGISTRY_API_VERSION = "v0"

    def search_official_registry(
        self, query: str = "", limit: int = 10
    ) -> list[MCPServerInfo]:
        """
        Search the official MCP Registry using the v0 API.

        Args:
            query: Search query (searches name, description, tags)
            limit: Maximum number of results

        Returns:
            List of MCPServerInfo objects
        """
        try:
            # Use the official /v0/servers endpoint
            url = f"{self.OFFICIAL_REGISTRY_URL}/{self.OFFICIAL_REGISTRY_API_VERSION}/servers"
            params: dict[str, str | int] = {"limit": limit}
            if query:
                params["search"] = query

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            results = []

            # The API returns servers in a 'servers' array
            servers = data.get("servers", [])

            for item in servers:
                # The actual server data is nested under a 'server' key
                server_data = item.get("server", {})

                # Extract relevant fields from the API response
                name = server_data.get("name", "")
                description = server_data.get("description", "")
                version = server_data.get("version", "")

                # Extract repository information
                repo_info = server_data.get("repository", {})
                if isinstance(repo_info, dict):
                    repository = repo_info.get("url", "")
                else:
                    repository = str(repo_info) if repo_info else ""

                # Extract package information (for installation)
                packages = server_data.get("packages", [])
                command = ""
                args = []
                server_url = ""
                install_command = ""

                if packages:
                    # Use the first stdio package if available
                    stdio_pkg = next(
                        (
                            p
                            for p in packages
                            if p.get("transport", {}).get("type") == "stdio"
                        ),
                        packages[0],
                    )

                    pkg_id = stdio_pkg.get("identifier", "")
                    registry_type = stdio_pkg.get("registryType", "")
                    runtime_hint = stdio_pkg.get("runtimeHint", "")

                    # Build command and install instructions
                    if registry_type == "npm":
                        command = runtime_hint or "npx"
                        args = [pkg_id]
                        install_command = f"npm install -g {pkg_id}"
                    elif registry_type == "pypi":
                        command = runtime_hint or "uvx"
                        args = [pkg_id]
                        install_command = f"pip install {pkg_id}"

                    # Add package arguments if needed
                    pkg_args = stdio_pkg.get("packageArguments", [])
                    for arg in pkg_args:
                        if arg.get("isRequired"):
                            arg_name = arg.get("name", "")
                            args.append(f"--{arg_name}")
                            if "default" in arg:
                                args.append(str(arg["default"]))

                # Extract tags (not in current API response, but might be added)
                tags = server_data.get("tags", [])
                author = server_data.get("author", "")

                results.append(
                    MCPServerInfo(
                        name=name,
                        description=description,
                        command=command,
                        args=args,
                        url=server_url,
                        registry="official",
                        tags=tags,
                        author=author,
                        repository=repository,
                        version=version,
                        install_command=install_command,
                    )
                )
            return results
        except requests.RequestException as e:
            logger.warning(f"Failed to search official registry: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to parse registry response: {e}")
            return []

    def search_mcp_so(self, query: str = "", limit: int = 10) -> list[MCPServerInfo]:
        """
        Search MCP.so directory.

        Note: MCP.so does not currently have a public API endpoint.
        This returns an empty list until the API becomes available.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of MCPServerInfo objects (currently empty)
        """
        # MCP.so appears to be a directory/showcase website without a public API
        # Users should visit https://mcp.so directly to browse servers
        logger.info(
            "MCP.so search: No public API available, visit https://mcp.so to browse servers"
        )
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
            name: Server name or ID

        Returns:
            MCPServerInfo object or None if not found
        """
        # First try to get by exact name from official registry
        results = self.search_official_registry(name, limit=50)

        # Look for exact name match
        for server in results:
            if server.name.lower() == name.lower():
                return server

        # If no exact match, try to get by ID if it looks like a UUID
        if len(name) > 20 and "-" in name:
            try:
                url = f"{self.OFFICIAL_REGISTRY_URL}/{self.OFFICIAL_REGISTRY_API_VERSION}/servers/{name}"
                response = requests.get(url, timeout=10)
                response.raise_for_status()

                item = response.json()
                config = item.get("config", {})
                metadata = item.get("metadata", {})

                return MCPServerInfo(
                    name=item.get("name", item.get("id", "")),
                    description=item.get("description", ""),
                    command=config.get("command", ""),
                    args=config.get("args", []),
                    url=config.get("url", ""),
                    registry="official",
                    tags=metadata.get("tags", []),
                    author=metadata.get("author", ""),
                    repository=item.get("repository", metadata.get("repository", "")),
                    version=metadata.get("version", ""),
                    install_command="",
                )
            except requests.RequestException:
                pass

        # Return first partial match if no exact match found
        if results:
            return results[0]

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
