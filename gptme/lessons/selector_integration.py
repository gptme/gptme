"""Lesson-specific implementation of context selector."""

from pathlib import Path
from typing import Any

from ..context_selector.base import ContextItem
from .parser import Lesson


class LessonItem(ContextItem):
    """Wrapper for lessons to work with context selector."""

    def __init__(self, lesson: Lesson):
        self.lesson = lesson

    @property
    def content(self) -> str:
        """Return lesson content for LLM evaluation."""
        return self.lesson.body

    @property
    def metadata(self) -> dict[str, Any]:
        """Return lesson metadata (YAML frontmatter + derived)."""
        return {
            "keywords": self.lesson.metadata.keywords,
            "tools": self.lesson.metadata.tools or [],
            "status": self.lesson.metadata.status,
            # Priority not in current schema (future enhancement)
            "priority": "normal",
            # Category from lesson path (e.g., "workflow", "tools")
            "category": self.lesson.category or "general",
            "path": str(self.lesson.path) if self.lesson.path else "unknown",
        }

    @property
    def identifier(self) -> str:
        """Return unique identifier for this lesson."""
        if self.lesson.path:
            # Use relative path from lessons directory as identifier
            # e.g., "workflow/git-workflow.md"
            path = Path(self.lesson.path)
            try:
                # Try to get relative path from lessons directory
                lessons_dir = path.parent
                while (
                    lessons_dir.name != "lessons" and lessons_dir.parent != lessons_dir
                ):
                    lessons_dir = lessons_dir.parent

                if lessons_dir.name == "lessons":
                    rel_path = path.relative_to(lessons_dir)
                    return str(rel_path)
            except (ValueError, AttributeError):
                pass

            # Fallback: use filename
            return path.name

        return "unknown-lesson"

    def __repr__(self) -> str:
        return f"LessonItem({self.identifier})"
