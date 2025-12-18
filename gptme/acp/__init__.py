"""ACP (Agent Client Protocol) support for gptme.

This module implements the Agent Client Protocol, allowing gptme to be used
as a coding agent from any ACP-compatible editor (Zed, JetBrains, etc.).

Usage:
    python -m gptme.acp

Or via CLI:
    gptme-acp
"""

from .agent import GptmeAgent

__all__ = ["GptmeAgent"]
