"""Triggered delivery of keyworded memories through the existing lesson matcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import MemoryEntry
    from .store import MemoryStore


@dataclass(frozen=True)
class MemoryMatch:
    """A living memory and the explicit keywords that triggered it."""

    entry: MemoryEntry
    score: float
    matched_by: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.entry.to_dict(),
            "body": self.entry.body,
            "score": self.score,
            "matched_by": self.matched_by,
        }


def match_memories(
    store: MemoryStore, text: str, *, limit: int = 5
) -> list[MemoryMatch]:
    """Match living entries by explicit keywords, using lesson wildcard semantics.

    Layering happens before lifecycle filtering, so a historical project entry
    still shadows a same-named user entry. Index selection is independent:
    unselected living memories remain eligible for triggered delivery.

    Memory names are identifiers, not skill names: only ``keywords`` participate
    in matching. No description/body similarity or tool-name bonus is added.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text.strip():
        return []

    entries = {
        entry.path: entry
        for entry in store.entries(status="living")
        if entry.keywords and entry.path is not None
    }
    if not entries:
        return []

    # Keep the lesson subsystem lazy for memory readers/writers that don't match.
    from ..lessons.matcher import LessonMatcher, MatchContext
    from ..lessons.parser import Lesson, LessonMetadata

    lessons = [
        Lesson(
            path=path,
            metadata=LessonMetadata(keywords=entry.keywords),
            title=entry.index_title,
            description=entry.description,
            category="memory",
            body=entry.body,
        )
        for path, entry in entries.items()
    ]
    return [
        MemoryMatch(entries[result.lesson.path], result.score, result.matched_by)
        for result in LessonMatcher().match(lessons, MatchContext(message=text))[:limit]
    ]


def render_matches(matches: list[MemoryMatch], *, body_chars: int = 1200) -> str:
    """Render bounded memory bodies with source paths for harness injection."""
    if body_chars <= 0:
        raise ValueError("body_chars must be positive")
    if not matches:
        return ""
    lines = ["<memory_triggered_entries>", "## Triggered Memories"]
    for hit in matches:
        entry = hit.entry
        lines.append(f"### {entry.index_title}")
        lines.append(f"Source: {entry.path} (scope: {entry.scope})")
        body = entry.body.strip() or entry.description
        if len(body) > body_chars:
            body = (
                body[: body_chars - 3].rstrip() + "..."
                if body_chars > 3
                else body[:body_chars]
            )
        lines.append(body)
    lines.append("</memory_triggered_entries>")
    return "\n\n".join(lines)
