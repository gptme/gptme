"""ACP (Agent Client Protocol) support for gptme.

This module implements the Agent Client Protocol, allowing gptme to be used
as a coding agent from any ACP-compatible editor (Zed, JetBrains, etc.).

Usage:
    python -m gptme.acp

Or via CLI:
    gptme-acp
"""

from .agent import GptmeAgent
from .types import (
    PermissionKind,
    PermissionOption,
    ToolCall,
    ToolCallStatus,
    ToolKind,
    gptme_tool_to_acp_kind,
)

__all__ = [
    "GptmeAgent",
    "PermissionKind",
    "PermissionOption",
    "ToolCall",
    "ToolCallStatus",
    "ToolKind",
    "gptme_tool_to_acp_kind",
]
