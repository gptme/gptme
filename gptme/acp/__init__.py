"""
ACP (Agent Client Protocol) server for gptme.

Implements the Agent Client Protocol so gptme can be discovered
in agent registries (Zed, JetBrains, etc.).
"""

from .server import ACPProtocolHandler, run_server

__all__ = ["ACPProtocolHandler", "run_server"]