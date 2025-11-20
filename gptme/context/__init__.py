"""Context management utilities.

This module provides:
- Unified context configuration (context.config)
- Context selection strategies (context.selector)
- Context compression utilities (context.compress)
- Plugin system for extensible context management (context.plugin)
- Plugin loader and registry (context.plugin_loader)
"""

from .compress import strip_reasoning
from .config import ContextConfig
from .plugin import ContextPlugin, TransformResult
from .plugin_loader import PluginPipeline, PluginRegistry, get_registry, register_plugin
from .plugins import CompressionPlugin
from .selector import ContextSelectorConfig

__all__ = [
    "CompressionPlugin",
    "ContextConfig",
    "ContextPlugin",
    "ContextSelectorConfig",
    "PluginPipeline",
    "PluginRegistry",
    "TransformResult",
    "get_registry",
    "register_plugin",
    "strip_reasoning",
]
