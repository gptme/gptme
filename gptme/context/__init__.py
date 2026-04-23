"""Context management utilities.

This module provides:
- Unified context configuration (context.config)
- Context selection strategies (context.selector)
- Context compression utilities (context.compress)
- Adaptive compression (context.adaptive_compressor)
- Task complexity analysis (context.task_analyzer)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adaptive_compressor import AdaptiveCompressor, CompressionResult
from .config import ContextConfig
from .selector import ContextSelectorConfig
from .task_analyzer import (
    TaskClassification,
    TaskFeatures,
    classify_task,
    extract_features,
)

if TYPE_CHECKING:
    pass

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

_lazy = {
    "strip_reasoning": (".compress", "strip_reasoning"),
}


def __getattr__(name: str):
    if name in _lazy:
        import importlib

        module_name, attr_name = _lazy[name]
        module = importlib.import_module(module_name, package=__package__)
        obj = getattr(module, attr_name)
        globals()[name] = obj
        return obj
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
