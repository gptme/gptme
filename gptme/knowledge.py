"""
Cross-session knowledge base: save and retrieve problem/resolution pairs.

Entries are stored as JSONL at ``~/.local/share/gptme/knowledge/entries.jsonl``
(respects XDG_DATA_HOME).  Each entry carries enough metadata for gptme-rag to
index it as a ``knowledge_entry`` source once that upstreaming work lands.

Retrieval without gptme-rag falls back to a simple keyword search over the
problem + resolution text.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from pathlib import Path

from .dirs import get_data_dir


class KnowledgeEntry(TypedDict):
    id: str
    problem: str
    resolution: str
    tags: list[str]
    keywords: list[str]
    created_at: str
    memory_type: str  # always "knowledge_entry" — used by gptme-rag source filter


def _knowledge_dir() -> Path:
    return get_data_dir() / "knowledge"


def _entries_file() -> Path:
    return _knowledge_dir() / "entries.jsonl"


def _load_entries() -> list[KnowledgeEntry]:
    path = _entries_file()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _append_entry(entry: KnowledgeEntry) -> None:
    path = _entries_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful words (length ≥ 4) from text for fast filtering."""
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", text.lower())
    seen: set[str] = set()
    result = []
    for w in words:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:30]


def knowledge_save(
    problem: str,
    resolution: str,
    tags: list[str] | None = None,
) -> KnowledgeEntry:
    """Save a problem/resolution pair to the cross-session knowledge base.

    Args:
        problem: Description of the problem or question.
        resolution: How it was resolved or answered.
        tags: Optional list of topic tags (e.g. ["git", "pytest"]).

    Returns:
        The saved entry dict.
    """
    if not problem.strip():
        raise ValueError("problem cannot be empty")
    if not resolution.strip():
        raise ValueError("resolution cannot be empty")

    keywords = _extract_keywords(f"{problem} {resolution}")
    entry: KnowledgeEntry = {
        "id": str(uuid.uuid4()),
        "problem": problem.strip(),
        "resolution": resolution.strip(),
        "tags": [t.strip() for t in (tags or []) if t.strip()],
        "keywords": keywords,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "memory_type": "knowledge_entry",
    }
    _append_entry(entry)
    return entry


def knowledge_search(
    query: str,
    top_k: int = 5,
    tags: list[str] | None = None,
) -> list[KnowledgeEntry]:
    """Search the knowledge base with simple keyword matching.

    Scores entries by counting how many query words appear in the combined
    problem + resolution + tags text.  Returns the top-k by score.

    Args:
        query: Free-text search query.
        top_k: Maximum number of results.
        tags: If given, only return entries that have ALL of these tags.

    Returns:
        Matching entries, highest-score first.
    """
    if not query.strip():
        raise ValueError("query cannot be empty")

    query_words = set(_extract_keywords(query))
    entries = _load_entries()

    # Tag filter
    if tags:
        required = {t.lower() for t in tags}
        entries = [
            e
            for e in entries
            if required.issubset({t.lower() for t in e.get("tags", [])})
        ]

    scored: list[tuple[int, KnowledgeEntry]] = []
    for entry in entries:
        haystack = " ".join(
            [entry.get("problem", ""), entry.get("resolution", "")]
            + entry.get("tags", [])
        ).lower()
        score = sum(1 for w in query_words if w in haystack)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:top_k]]


def knowledge_list(
    tags: list[str] | None = None,
    limit: int = 50,
) -> list[KnowledgeEntry]:
    """List knowledge entries, newest first.

    Args:
        tags: If given, only return entries that have ALL of these tags.
        limit: Maximum number of entries to return.

    Returns:
        Entries sorted by creation date descending.
    """
    entries = _load_entries()
    if tags:
        required = {t.lower() for t in tags}
        entries = [
            e
            for e in entries
            if required.issubset({t.lower() for t in e.get("tags", [])})
        ]
    entries = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)
    return entries[:limit]


def knowledge_delete(entry_id: str) -> bool:
    """Remove an entry by ID, rewriting the JSONL file.

    Returns True if the entry was found and deleted, False otherwise.
    """
    entries = _load_entries()
    kept = [e for e in entries if e.get("id") != entry_id]
    if len(kept) == len(entries):
        return False

    path = _entries_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in kept:
            f.write(json.dumps(e) + "\n")
    return True
