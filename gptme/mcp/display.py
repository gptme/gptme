"""Credential-safe display of MCP server targets.

Shared by the ``gptme-util mcp`` CLI and the conversational ``mcp`` tool so a
configured URL never leaks embedded credentials into terminals or into the
model's context.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from ..config import MCPServerConfig

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


def redact_url(url: str | None) -> str:
    """Render a URL for display, hiding embedded credentials.

    Redacts userinfo (``user:pass@``) and every query/fragment parameter value.
    Never raises: a malformed port or an IPv6 host must not abort the
    diagnostic before it is even shown.
    """
    try:
        parts = urlsplit(url or "")
    except ValueError:
        # e.g. an unmatched IPv6 bracket. The URL can't be parsed to
        # redact it safely, so show nothing from it rather than abort.
        return "<unparseable URL>"
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


def display_target(server: MCPServerConfig) -> str:
    """Render a server target for display, hiding URL credentials.

    HTTP servers go through :func:`redact_url`; stdio command arguments are
    shown verbatim because they define the command being run.
    """
    if server.is_http:
        return redact_url(server.url)
    return shlex.join([server.command or "", *server.args])
