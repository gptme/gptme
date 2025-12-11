"""Context management utilities.

This module provides:
- Unified context configuration (context.config)
- Context selection strategies (context.selector)
- Context compression utilities (context.compress)
- Adaptive compression (context.adaptive_compressor)
- Task complexity analysis (context.task_analyzer)
"""

from .adaptive_compressor import AdaptiveCompressor, CompressionResult
from .compress import strip_reasoning
from .config import ContextConfig
from .selector import ContextSelectorConfig
from .task_analyzer import (
    TaskClassification,
    TaskFeatures,
    classify_task,
    extract_features,
)

__all__ = [
    "ContextConfig",
    "ContextSelectorConfig",
    "strip_reasoning",
    "AdaptiveCompressor",
    "CompressionResult",
    "TaskClassification",
    "TaskFeatures",
    "classify_task",
    "extract_features",
]
