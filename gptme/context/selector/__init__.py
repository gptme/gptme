"""Context selection utilities for choosing relevant lessons, files, and other context.

This module provides a general-purpose framework for selecting the most relevant
context items using different strategies (rule-based, LLM-based, hybrid).

Example:
    from gptme.context.selector import HybridSelector, ContextSelectorConfig

    config = ContextSelectorConfig(strategy="hybrid")
    selector = HybridSelector(config)

    selected = await selector.select(
        query="How do I use the patch tool?",
        candidates=lesson_items,
        max_results=5,
    )
"""

from __future__ import annotations

from .base import ContextItem, ContextSelector
from .config import ContextSelectorConfig
from .file_config import FileSelectorConfig

# Heavy imports that pull in gptme.message and other heavy deps are lazy to
# break the gptme.config → gptme.context.selector → gptme.message circular
# dependency that surfaces when gptme.__init__ is itself lazily imported.

__all__ = [
    "ContextItem",
    "ContextSelector",
    "ContextSelectorConfig",
    "FileSelectorConfig",
    "FileItem",
    "RuleBasedSelector",
    "LLMSelector",
    "HybridSelector",
    "select_relevant_files",
]

_lazy = {
    "FileItem": (".file_integration", "FileItem"),
    "select_relevant_files": (".file_selector", "select_relevant_files"),
    "HybridSelector": (".hybrid", "HybridSelector"),
    "LLMSelector": (".llm_based", "LLMSelector"),
    "RuleBasedSelector": (".rule_based", "RuleBasedSelector"),
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
