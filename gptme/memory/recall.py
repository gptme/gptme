"""Lexical recall over a :class:`~gptme.memory.store.MemoryStore`.

The optional gptme-rag TF-IDF backend is preferred when installed. A small
stdlib token-overlap scorer keeps ``gptme-util memory recall`` useful in a
minimal gptme installation and makes the fallback explicit in every result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .schema import MemoryEntry
    from .store import MemoryStore

RecallBackend = Literal["auto", "tfidf", "overlap"]
DEFAULT_BODY_CHARS = 1200
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "for",
        "from",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "them",
        "this",
        "to",
        "use",
        "when",
        "with",
        "you",
        "your",
    }
)


class RecallBackendUnavailable(ImportError):
    """Raised when a caller forces a recall backend that is not installed."""


@dataclass(frozen=True)
class RecallHit:
    """One ranked memory entry."""

    entry: MemoryEntry
    score: float
    matched_terms: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.entry.to_dict(),
            "score": self.score,
            "matched_terms": self.matched_terms,
            "body": self.entry.body,
        }


@dataclass(frozen=True)
class RecallResult:
    """Ranked hits plus the backend that actually produced them."""

    backend: Literal["tfidf", "overlap"]
    hits: list[RecallHit]

    def to_dict(self) -> dict[str, object]:
        return {"backend": self.backend, "hits": [hit.to_dict() for hit in self.hits]}


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _entry_text(entry: MemoryEntry) -> str:
    return "\n".join(
        part
        for part in (
            entry.name.replace("-", " ").replace("_", " "),
            entry.title or "",
            entry.description,
            " ".join(entry.keywords),
            entry.body,
        )
        if part
    )


def _recall_overlap(
    entries: list[MemoryEntry], query: str, limit: int
) -> list[RecallHit]:
    query_tokens = _tokens(query)
    if len(query_tokens) < 2:
        return []

    hits: list[RecallHit] = []
    for entry in entries:
        matched = sorted(query_tokens & _tokens(_entry_text(entry)))
        if len(matched) < 2:
            continue
        score = round(len(matched) / len(query_tokens), 4)
        hits.append(RecallHit(entry=entry, score=score, matched_terms=matched[:5]))
    return sorted(hits, key=lambda hit: (-hit.score, hit.entry.name))[:limit]


def _install_hint() -> str:
    return "install gptme-rag[lexical] or use --backend overlap"


def _recall_tfidf(
    entries: list[MemoryEntry], query: str, limit: int
) -> list[RecallHit]:
    try:
        from gptme_rag.indexing.document import Document
        from gptme_rag.lexical import TfidfIndex
    except ImportError as exc:
        raise RecallBackendUnavailable(
            f"gptme-rag lexical retrieval is unavailable; {_install_hint()}"
        ) from exc

    documents = [
        Document(
            content=_entry_text(entry),
            metadata={"memory_entry": entry},
            source_path=entry.path,
            doc_id=entry.name,
        )
        for entry in entries
    ]
    try:
        index = TfidfIndex(relevance_floor=0.05)
        index.index(documents)
        ranked = index.search(query, n_results=limit)
    except ImportError as exc:
        raise RecallBackendUnavailable(
            f"gptme-rag lexical retrieval is unavailable; {_install_hint()}"
        ) from exc

    return [
        RecallHit(
            entry=hit.document.metadata["memory_entry"],
            score=float(hit.score),
            matched_terms=[],
        )
        for hit in ranked
    ]


def recall(
    store: MemoryStore,
    query: str,
    *,
    limit: int = 3,
    backend: RecallBackend = "auto",
) -> RecallResult:
    """Recall living entries across every layered root.

    ``auto`` prefers gptme-rag's TF-IDF backend and falls back to the built-in
    overlap scorer only when the optional backend is unavailable. A forced
    ``tfidf`` request fails instead of silently changing semantics.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    query = query.strip()
    entries = store.entries(status="living")

    if backend in {"auto", "tfidf"}:
        try:
            return RecallResult(
                backend="tfidf", hits=_recall_tfidf(entries, query, limit)
            )
        except RecallBackendUnavailable:
            if backend == "tfidf":
                raise

    return RecallResult(backend="overlap", hits=_recall_overlap(entries, query, limit))


def render_recall(result: RecallResult, *, body_chars: int = DEFAULT_BODY_CHARS) -> str:
    """Render bounded context suitable for injection into another harness."""
    if body_chars <= 0:
        raise ValueError("body_chars must be positive")
    if not result.hits:
        return ""
    lines = [
        "<memory_relevant_entries>",
        f"## Relevant Memory (backend={result.backend})",
    ]
    for hit in result.hits:
        entry = hit.entry
        lines.append(
            f"- [{entry.type}] {entry.name} "
            f"(score {hit.score:.3f}, scope {entry.scope or 'unknown'})"
        )
        if hit.matched_terms:
            lines.append(f"  Match: {', '.join(hit.matched_terms)}")
        summary = re.sub(r"\s+", " ", entry.body.strip() or entry.description)
        if summary:
            if len(summary) > body_chars:
                if body_chars <= 3:
                    summary = summary[:body_chars]
                else:
                    summary = summary[: body_chars - 3].rstrip() + "..."
            lines.append(f"  {summary}")
    lines.append("</memory_relevant_entries>")
    return "\n".join(lines)
