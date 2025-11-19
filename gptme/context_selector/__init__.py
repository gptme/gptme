"""Context selection utilities for choosing relevant lessons, files, and other context.

This module provides a general-purpose framework for selecting the most relevant
context items using different strategies (rule-based, LLM-based, hybrid).

Example:
    from gptme.context_selector import HybridSelector, ContextSelectorConfig

    config = ContextSelectorConfig(strategy="hybrid")
    selector = HybridSelector(config)

    selected = await selector.select(
        query="How do I use the patch tool?",
        candidates=lesson_items,
        max_results=5,
    )
"""

from .base import ContextItem, ContextSelector
from .config import ContextSelectorConfig
from .file_config import FileSelectorConfig
from .file_integration import FileItem
from .file_selector import select_relevant_files
from .hybrid import HybridSelector
from .llm_based import LLMSelector
from .rule_based import RuleBasedSelector

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
