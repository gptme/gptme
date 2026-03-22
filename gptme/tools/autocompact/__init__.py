"""
Auto-compacting tool for handling conversations with massive tool results.

Automatically triggers when conversation has massive tool results that would
prevent resumption, compacting them to allow the conversation to continue.
"""

from .decision import (
    MIN_SAVINGS_RATIO,
    CompactAction,
    estimate_compaction_savings,
    should_auto_compact,
)
from .engine import auto_compact_log
from .handlers import _compact_resume, cmd_compact_handler
from .hook import _get_compacted_name, autocompact_hook, tool
from .resume import _load_context_files, _parse_context_files, _resume_via_llm
from .scoring import (
    _score_reference_potential,
    _score_semantic_importance,
    compress_content,
    extract_code_blocks,
    score_sentence,
)

__all__ = [
    # Tool registration
    "tool",
    # Scoring & compression
    "compress_content",
    "extract_code_blocks",
    "score_sentence",
    "_score_semantic_importance",
    "_score_reference_potential",
    # Decision logic
    "CompactAction",
    "MIN_SAVINGS_RATIO",
    "estimate_compaction_savings",
    "should_auto_compact",
    # Engine
    "auto_compact_log",
    # Resume
    "_parse_context_files",
    "_load_context_files",
    "_resume_via_llm",
    # Handlers
    "cmd_compact_handler",
    "_compact_resume",
    # Hook
    "autocompact_hook",
    "_get_compacted_name",
]
