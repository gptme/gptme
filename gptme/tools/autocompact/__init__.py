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
from .engine import (
    auto_compact_log,
    prune_stale_tool_outputs,
    shadow_prune_stale_tool_outputs,
)
from .events import append_compaction_event, read_compaction_events
from .handlers import (
    _compact_resume,
    _compact_summarize,
    _compact_trim,
    cmd_compact_handler,
)
from .hook import _get_compacted_name, autocompact_hook, tool
from .resume import _load_context_files, _parse_context_files, _resume_via_llm
from .scoring import (
    PruneDecision,
    _score_reference_potential,
    _score_semantic_importance,
    compress_content,
    extract_code_blocks,
    score_sentence,
    score_tool_output_relevance,
)

__all__ = [
    # Tool registration
    "tool",
    # Scoring & compression
    "compress_content",
    "extract_code_blocks",
    "score_sentence",
    "score_tool_output_relevance",
    "_score_semantic_importance",
    "_score_reference_potential",
    # Decision logic
    "CompactAction",
    "MIN_SAVINGS_RATIO",
    "estimate_compaction_savings",
    "should_auto_compact",
    # Engine
    "auto_compact_log",
    "prune_stale_tool_outputs",
    "shadow_prune_stale_tool_outputs",
    # Shadow / evaluation
    "PruneDecision",
    # Observability
    "append_compaction_event",
    "read_compaction_events",
    # Resume
    "_parse_context_files",
    "_load_context_files",
    "_resume_via_llm",
    # Handlers
    "cmd_compact_handler",
    "_compact_trim",
    "_compact_summarize",
    "_compact_resume",  # deprecated alias for _compact_summarize
    # Hook
    "autocompact_hook",
    "_get_compacted_name",
]
