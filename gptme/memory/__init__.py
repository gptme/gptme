"""Cross-harness memory store: CC-compatible markdown entries with layered roots.

Storage depends on :mod:`gptme.dirs`; triggered delivery lazily adapts entries
to :class:`gptme.lessons.matcher.LessonMatcher`. See gptme/gptme#3734.
"""

from .recall import (
    RecallBackendUnavailable,
    RecallHit,
    RecallResult,
    recall,
    render_recall,
)
from .roots import MemoryRoot, resolve_roots
from .schema import (
    MemoryEntry,
    MemoryFrontmatterError,
    MemoryParseError,
    parse_entry,
    slugify,
)
from .store import AuditIssue, MemoryStore, update_index_line

__all__ = [
    "AuditIssue",
    "MemoryEntry",
    "MemoryFrontmatterError",
    "MemoryParseError",
    "MemoryRoot",
    "MemoryStore",
    "RecallBackendUnavailable",
    "RecallHit",
    "RecallResult",
    "parse_entry",
    "recall",
    "render_recall",
    "resolve_roots",
    "slugify",
    "update_index_line",
]
