"""
URI support for Message.files - handles URIs alongside filesystem Paths.

URIs (like http://, https://, memo://) cannot be treated as filesystem paths.
This module provides a clean URI class that explicitly distinguishes URIs from Paths,
avoiding the design issues of trying to subclass Path for URI handling.

Usage:
    from gptme.util.uri import URI, is_uri, FilePath

    # Check if string is a URI
    if is_uri("http://example.com"):
        uri = URI("http://example.com")

    # Type hint for files that can be Path or URI
    files: list[FilePath] = [Path("local.txt"), URI("http://example.com")]
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

# Common URI schemes (for documentation - not used for validation)
# MCP servers can register arbitrary schemes per the spec, so validation
# uses RFC3986-compliant pattern detection (scheme://) instead of a whitelist.
# file:// is intentionally excluded - use Path for local files
URI_SCHEMES = frozenset(
    [
        "http",
        "https",
        "ftp",
        "ftps",
        "memo",  # MCP resources (example custom scheme)
        "mcp",  # MCP resources (example custom scheme)
        "data",  # data URIs
        "git",  # Git resources
        "ws",  # WebSocket
        "wss",  # WebSocket Secure
    ]
)

# Pattern to detect URIs: scheme://...
URI_PATTERN = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):\/\/")


def is_uri(s: str | Path) -> bool:
    """Check if a string looks like a URI (has scheme://)."""
    if isinstance(s, Path):
        return False
    return bool(URI_PATTERN.match(str(s)))


@dataclass(frozen=True)
class URI:
    """
    A URI reference (not a filesystem path).

    Accepts any RFC3986-compliant URI with a scheme:// prefix. This includes
    standard schemes (http, https, ftp) and custom schemes that MCP servers
    may register (memo, mcp, or any arbitrary scheme).

    Unlike Path, URI does not support filesystem operations like exists(),
    read_text(), etc. URIs must be handled separately through appropriate
    mechanisms (HTTP fetch, MCP resource resolution, etc.).

    This class is intentionally minimal - it's a marker type that indicates
    "this is a URI, not a local file path."
    """

    uri: str

    def __post_init__(self):
        if not is_uri(self.uri):
            raise ValueError(f"Invalid URI (must have scheme://): {self.uri}")

    @property
    def scheme(self) -> str:
        """Extract the URI scheme (http, https, memo, etc.)."""
        match = URI_PATTERN.match(self.uri)
        if match:
            return match.group(1).lower()
        return ""

    @property
    def is_http(self) -> bool:
        """Check if this is an HTTP(S) URL."""
        return self.scheme in ("http", "https")

    @property
    def is_mcp(self) -> bool:
        """Check if this is an MCP resource URI."""
        return self.scheme in ("memo", "mcp")

    def __str__(self) -> str:
        return self.uri

    def __repr__(self) -> str:
        return f"URI({self.uri!r})"

    def __hash__(self) -> int:
        return hash(self.uri)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, URI):
            return self.uri == other.uri
        if isinstance(other, str):
            return self.uri == other
        return False


# Type alias for files that can be either Path or URI
FilePath: TypeAlias = Path | URI


def parse_file_reference(s: str) -> FilePath:
    """
    Parse a string into either a URI or a Path.

    Args:
        s: A string that could be a URI or a filesystem path

    Returns:
        URI if the string has a URI scheme, otherwise Path
    """
    # Ensure we have a regular Python str, not tomlkit.String or similar
    s = str(s)
    if is_uri(s):
        return URI(s)
    return Path(s)
