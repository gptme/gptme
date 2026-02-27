"""ACP (Agent Client Protocol) support for gptme.

gptme implements ACP in two roles:

- **Agent** (server side): ``gptme-acp`` / ``python -m gptme.acp`` exposes
  gptme as an ACP-compatible agent that editors (Zed, JetBrains, …) can
  connect to.

- **Client** (client side): :class:`~gptme.acp.client.GptmeAcpClient` lets
  gptme spawn *other* ACP agents (including gptme itself) as isolated
  subprocesses.  This is useful for per-session cwd isolation in the HTTP
  server and for multi-harness subagent support.
"""

from .agent import GptmeAgent
from .client import GptmeAcpClient, acp_client
from .types import (
    PermissionKind,
    PermissionOption,
    ToolCall,
    ToolCallStatus,
    ToolKind,
    gptme_tool_to_acp_kind,
)

__all__ = [
    # Agent (server) side
    "GptmeAgent",
    # Client side
    "GptmeAcpClient",
    "acp_client",
    # Types
    "PermissionKind",
    "PermissionOption",
    "ToolCall",
    "ToolCallStatus",
    "ToolKind",
    "gptme_tool_to_acp_kind",
]
