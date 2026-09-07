"""Cross-harness memory store: CC-compatible markdown entries with layered roots.

This package imports nothing from gptme core except :mod:`gptme.dirs`, so it
can be extracted into a standalone ``gptme-memory`` package (sibling of
``gptme-rag``) as a file move. See gptme/gptme#3734.
"""

from .roots import MemoryRoot, resolve_roots
from .schema import MemoryEntry, MemoryParseError, parse_entry, slugify
from .store import MemoryStore, update_index_line

__all__ = [
    "MemoryEntry",
    "MemoryParseError",
    "MemoryRoot",
    "MemoryStore",
    "parse_entry",
    "resolve_roots",
    "slugify",
    "update_index_line",
]
