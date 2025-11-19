"""Context management for gptme.

This module provides:
- Unified context configuration (context.config)
- Context selection strategies (context.selector)
"""

from .config import ContextConfig
from .selector import ContextSelectorConfig

__all__ = [
    "ContextConfig",
    "ContextSelectorConfig",
]
