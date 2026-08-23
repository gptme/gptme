"""Cross-session knowledge base.

Sessions save resolved errors/workarounds to a local JSONL file so future
sessions can retrieve and reuse them instead of re-solving the same problem
from scratch.

Schema follows ``knowledge/technical-designs/2026-08-13-gptme-cross-session-kb-schema.md``
(idea #1050). This is the personal, semantic-query complement to the global,
keyword-triggered lesson system.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .dirs import get_data_dir

if TYPE_CHECKING:
    from pathlib import Path

# Validation limits (mirror the schema doc).
MAX_PROBLEM = 200
MAX_CONTEXT = 2000
MAX_RESOLUTION = 5000

# Rejects URLs (prevents link/phishing injection).
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class KnowledgeValidationError(ValueError):
    """Raised when an entry field violates the schema contract."""


@dataclass
class KnowledgeEntry:
    """A single resolved problem in the knowledge base."""

    id: str
    problem: str
    resolution: str
    problem_tags: list[str] = field(default_factory=list)
    context: str = ""
    verified_at: str = ""
    session_id: str = ""
    model: str = ""
    keywords: list[str] = field(default_factory=list)

    @property
    def search_text(self) -> str:
        """Text used for retrieval scoring."""
        parts = [self.problem, self.resolution, self.context]
        parts.extend(self.problem_tags)
        parts.extend(self.keywords)
        return " ".join(p for p in parts if p)


def get_knowledge_file() -> Path:
    """Return the path to the knowledge entries file."""
    return get_data_dir() / "knowledge" / "entries.jsonl"


def _validate_text(text: str, field_name: str, max_len: int, *, required: bool) -> None:
    if required and not text.strip():
        raise KnowledgeValidationError(f"{field_name} is required and cannot be empty.")
    if not text.isprintable():
        raise KnowledgeValidationError(f"{field_name} must be printable text.")
    if len(text) > max_len:
        raise KnowledgeValidationError(f"{field_name} exceeds {max_len} characters.")
    if _URL_RE.search(text):
        raise KnowledgeValidationError(f"{field_name} must not contain URLs.")


def validate_entry(
    problem: str,
    resolution: str,
    context: str = "",
    problem_tags: list[str] | None = None,
    keywords: list[str] | None = None,
) -> None:
    """Validate entry fields against the schema contract."""
    _validate_text(problem, "problem", MAX_PROBLEM, required=True)
    _validate_text(resolution, "resolution", MAX_RESOLUTION, required=True)
    if context:
        _validate_text(context, "context", MAX_CONTEXT, required=False)
    for tag_list in (problem_tags or [], keywords or []):
        for tag in tag_list:
            if not tag.strip():
                raise KnowledgeValidationError("Tags and keywords must be non-empty.")


def save_entry(
    problem: str,
    resolution: str,
    context: str = "",
    problem_tags: list[str] | None = None,
    keywords: list[str] | None = None,
    session_id: str = "",
    model: str = "",
) -> KnowledgeEntry:
    """Append a new entry to the knowledge base and return it."""
    validate_entry(problem, resolution, context, problem_tags, keywords)
    entry = KnowledgeEntry(
        id=str(uuid.uuid4()),
        problem=problem.strip(),
        resolution=resolution.strip(),
        context=context.strip(),
        problem_tags=[t.strip() for t in (problem_tags or [])],
        keywords=[k.strip() for k in (keywords or [])],
        verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        session_id=session_id,
        model=model,
    )
    path = get_knowledge_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
    return entry


def load_entries() -> list[KnowledgeEntry]:
    """Load all entries from the knowledge base (empty if missing/corrupt)."""
    path = get_knowledge_file()
    if not path.exists():
        return []
    entries: list[KnowledgeEntry] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    continue
                problem_tags = data.get("problem_tags", [])
                if not isinstance(problem_tags, list):
                    problem_tags = []
                keywords = data.get("keywords", [])
                if not isinstance(keywords, list):
                    keywords = []
                entries.append(
                    KnowledgeEntry(
                        id=data.get("id", ""),
                        problem=data.get("problem", ""),
                        resolution=data.get("resolution", ""),
                        problem_tags=problem_tags,
                        context=data.get("context", ""),
                        verified_at=data.get("verified_at", ""),
                        session_id=data.get("session_id", ""),
                        model=data.get("model", ""),
                        keywords=keywords,
                    )
                )
            except (json.JSONDecodeError, TypeError, AttributeError):
                # Skip corrupt lines; the KB degrades gracefully.
                continue
    return entries


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_\-]+", text.lower())


def search_entries(query: str, top_k: int = 5) -> list[KnowledgeEntry]:
    """Return top-k entries by term-overlap relevance to ``query``.

    Simple stdlib BM25-ish scoring (term frequency weighting with an idf-style
    boost for rare terms). Per the schema's "graceful degradation" constraint
    this needs no external dependencies.
    """
    entries = load_entries()
    if not entries:
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    doc_freq: Counter[str] = Counter()
    for e in entries:
        for tok in set(_tokenize(e.search_text)):
            doc_freq[tok] += 1

    n_docs = len(entries)
    scored: list[tuple[float, KnowledgeEntry]] = []
    for e in entries:
        counts = Counter(_tokenize(e.search_text))
        score = 0.0
        for tok in query_tokens:
            tf = counts.get(tok, 0)
            if not tf:
                continue
            df = doc_freq.get(tok, 1)
            # idf-style boost: rarer terms weight more; floor at 1 to avoid 0.
            idf = max(1.0, math.log((n_docs + 1) / (df + 0.5)))
            score += tf * idf
        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:top_k]]
