"""
Cross-session knowledge base: save and retrieve problem/resolution pairs.

Entries are stored as JSONL at ``~/.local/share/gptme/knowledge/entries.jsonl``
(respects XDG_DATA_HOME).  Each entry carries ``memory_type="knowledge_entry"``
so gptme-rag's ``KnowledgeEntrySource`` can index the same JSONL.

Retrieval without gptme-rag uses keyword search over problem + resolution
text. Matching entries are injected at session start (see
``gptme.hooks.knowledge_inject``) when the initial prompt has enough
signal to search.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Iterator

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


_thread_lock = threading.Lock()


@contextlib.contextmanager
def _exclusive_lock() -> Iterator[None]:
    """Advisory exclusive lock protecting concurrent saves and deletes.

    Uses fcntl.flock on Unix for cross-process mutual exclusion.  On Windows
    (no fcntl), falls back to a module-level threading.Lock which prevents
    same-process thread races; cross-process races on Windows are accepted as
    the tool is designed for single-user personal use.
    """
    lock_path = _knowledge_dir() / ".entries.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl as _fcntl  # Unix only

        with _thread_lock, lock_path.open("w") as lf:
            _fcntl.flock(lf, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lf, _fcntl.LOCK_UN)
    except ImportError:
        with _thread_lock:
            yield  # Windows: thread-safe, not cross-process-safe


def _is_valid_entry(parsed: object) -> bool:
    """Return whether parsed JSON has the fields required by the store."""
    if not isinstance(parsed, dict):
        return False
    try:
        uuid.UUID(parsed.get("id", ""))
    except (AttributeError, TypeError, ValueError):
        return False
    return all(
        isinstance(parsed.get(key), expected_type)
        for key, expected_type in (
            ("problem", str),
            ("resolution", str),
            ("tags", list),
            ("created_at", str),
        )
    ) and all(isinstance(tag, str) for tag in parsed["tags"])


def _load_entries() -> list[KnowledgeEntry]:
    path = _entries_file()
    if not path.exists():
        return []
    entries = []
    # errors="replace" keeps a corrupted (non-UTF-8) store from crashing
    # list/search/save; invalid bytes become U+FFFD and those lines fail
    # json.loads and are skipped, matching the malformed-object policy.
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                parsed = json.loads(line)
                if _is_valid_entry(parsed):
                    entries.append(cast(KnowledgeEntry, parsed))
            except json.JSONDecodeError:
                pass
    return entries


def _load_entries_locked() -> list[KnowledgeEntry]:
    """Load entries under the lock, falling back if the directory is read-only.

    Search and list are read paths: a read-only knowledge directory (or a
    lock file we cannot create) must still return stored entries rather than
    raising PermissionError from opening the lock with mode ``"w"``.
    """
    try:
        with _exclusive_lock():
            return _load_entries()
    except PermissionError:
        return _load_entries()


def _append_entry(entry: KnowledgeEntry) -> None:
    path = _entries_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(), path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _extract_keywords(text: str) -> list[str]:
    """Extract unique words, numeric IDs, and underscore-delimited parts.

    ``foo_bar`` yields ``foo_bar``, ``foo``, and ``bar`` so a query for the
    common prefix ``foo`` still matches without falling back to substring
    matching (which would also match ``git`` inside ``digit``).
    """
    words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    parts: list[str] = []
    for word in words:
        parts.append(word)
        if "_" in word:
            parts.extend(part for part in word.split("_") if part)
    return list(dict.fromkeys(parts))


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
    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    query_words = set(_extract_keywords(query))
    entries = _load_entries_locked()

    # Tag filter (strip to match knowledge_save's stored tags)
    if tags:
        required = {t.strip().lower() for t in tags if t.strip()}
        if required:
            entries = [
                e
                for e in entries
                if required.issubset({t.lower() for t in e.get("tags", [])})
            ]

    scored: list[tuple[int, KnowledgeEntry]] = []
    for entry in entries:
        haystack_tokens = set(
            _extract_keywords(
                " ".join(
                    [entry.get("problem", ""), entry.get("resolution", "")]
                    + entry.get("tags", [])
                )
            )
        )
        score = sum(1 for w in query_words if w in haystack_tokens)
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
    entries = _load_entries_locked()
    if tags:
        required = {t.strip().lower() for t in tags if t.strip()}
        if required:
            entries = [
                e
                for e in entries
                if required.issubset({t.lower() for t in e.get("tags", [])})
            ]
    entries = sorted(entries, key=lambda e: e.get("created_at", ""), reverse=True)
    return entries[:limit]


_DEFAULT_SESSION_TOP_K = 3
_MAX_FIELD_CHARS = 240
_MIN_QUERY_KEYWORDS = 2


def _clip(text: str, limit: int = _MAX_FIELD_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def select_knowledge_for_session(
    query: str | None,
    *,
    top_k: int = _DEFAULT_SESSION_TOP_K,
) -> list[KnowledgeEntry]:
    """Return KB entries matching *query* for session-start injection.

    Conservative: no query, too-few keywords, or a search error yields
    nothing. Recency fallback is omitted so a greeting does not dump
    unrelated entries into the prompt.
    """
    if not query or not query.strip():
        return []
    keywords = _extract_keywords(query)
    if len(keywords) < _MIN_QUERY_KEYWORDS:
        return []
    try:
        return knowledge_search(query, top_k=top_k)
    except (OSError, ValueError):
        return []


def format_knowledge_prompt(entries: list[KnowledgeEntry]) -> str:
    """Format matching KB entries as a compact system-prompt block."""
    if not entries:
        return ""
    lines = [
        "<knowledge-entries>",
        "Relevant saved knowledge from previous sessions (not project docs):",
        "",
    ]
    for i, entry in enumerate(entries, start=1):
        problem = _clip(str(entry.get("problem", "")))
        resolution = _clip(str(entry.get("resolution", "")))
        lines.append(f"{i}. {problem}")
        lines.append(f"   {resolution}")
        tags = [t for t in entry.get("tags", []) if isinstance(t, str) and t.strip()]
        if tags:
            lines.append(f"   tags: {_clip(', '.join(tags))}")
        lines.append("")
    lines.append("</knowledge-entries>")
    return "\n".join(lines).rstrip() + "\n"


def _replace_entries(path: Path, entries: list[KnowledgeEntry]) -> None:
    """Atomically replace the entry store and clean up failed temp writes."""
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tf:
            tmp_path = tf.name
            for entry in entries:
                tf.write(json.dumps(entry) + "\n")
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def knowledge_delete(entry_id: str) -> bool:
    """Remove an entry by ID, rewriting the JSONL file atomically.

    Holds an exclusive advisory lock (Unix: fcntl.flock) for the entire
    read-filter-write cycle so a concurrent save cannot append between the
    snapshot read and the atomic replace.

    Returns True if the entry was found and deleted, False otherwise.
    """
    with _exclusive_lock():
        entries = _load_entries()
        kept = [e for e in entries if e.get("id") != entry_id]
        if len(kept) == len(entries):
            return False

        path = _entries_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_entries(path, kept)
    return True


def knowledge_delete_by_prefix(
    prefix: str,
) -> tuple[str | None, str, list[KnowledgeEntry]]:
    """Delete an entry by ID prefix, atomically under the exclusive lock.

    Holds the exclusive lock for the entire prefix-resolve + delete cycle so
    that a concurrent save or delete cannot change the entry set between the
    prefix lookup and the actual write.

    Returns:
        (deleted_id, status, matches) where status is one of:
        - ``'deleted'``: entry found and removed; ``deleted_id`` is the full ID.
        - ``'ambiguous'``: prefix matched more than one entry; ``matches``
          contains all candidates so the caller can list them.
        - ``'not_found'``: no entry matched the prefix.
    """
    if not prefix.strip():
        raise ValueError("entry_id cannot be empty")
    with _exclusive_lock():
        entries = _load_entries()
        matches = [e for e in entries if e.get("id", "").startswith(prefix)]
        if len(matches) > 1:
            return None, "ambiguous", matches
        if not matches:
            return None, "not_found", []

        full_id = matches[0]["id"]
        kept = [e for e in entries if e.get("id") != full_id]

        path = _entries_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace_entries(path, kept)
    return full_id, "deleted", []
