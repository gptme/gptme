"""Context management utilities.

This module provides:
- Unified context configuration (context.config)
- Context selection strategies (context.selector)
- Context compression utilities (context.compress)
"""

from .compress import strip_reasoning
from .config import ContextConfig
from .selector import ContextSelectorConfig

__all__ = ["ContextConfig", "ContextSelectorConfig", "strip_reasoning"]
