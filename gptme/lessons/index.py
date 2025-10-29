"""Lesson index for discovery and search."""

import logging
from pathlib import Path

from .parser import Lesson, parse_lesson

logger = logging.getLogger(__name__)


class LessonIndex:
    """Index of available lessons with search capabilities."""

    def __init__(self, lesson_dirs: list[Path] | None = None):
        """Initialize lesson index.

        Args:
            lesson_dirs: Directories to search for lessons.
                         If None, uses default locations.
        """
        self.lesson_dirs = lesson_dirs or self._default_dirs()
        self.lessons: list[Lesson] = []
        self._index_lessons()

    @staticmethod
    def _default_dirs() -> list[Path]:
        """Get default lesson directories.

        Searches for lessons in:
        - User config: ~/.config/gptme/lessons
        - Current workspace: ./lessons
        - Project-local: ./.gptme/lessons
        - Configured directories from gptme.toml

        Also detects .cursorrules files and provides conversion guidance.
        """
        from pathlib import Path

        from ..config import get_config

        dirs = []

        # User config directory
        config_dir = Path.home() / ".config" / "gptme" / "lessons"
        if config_dir.exists():
            dirs.append(config_dir)

        # Current workspace
        workspace_dir = Path.cwd() / "lessons"
        if workspace_dir.exists():
            dirs.append(workspace_dir)

        # Project-local lessons (.gptme/lessons/)
        gptme_lessons_dir = Path.cwd() / ".gptme" / "lessons"
        if gptme_lessons_dir.exists():
            dirs.append(gptme_lessons_dir)

        # Check for .cursorrules file and provide guidance
        cursorrules_file = Path.cwd() / ".cursorrules"
        if cursorrules_file.exists():
            logger.info(
                "Found .cursorrules file in project root.\n"
                "To use with gptme, convert to lesson format:\n"
                "  cd gptme-contrib/cursorrules\n"
                "  python3 cursorrules_parser.py to-lesson /path/to/.cursorrules .gptme/lessons/project-rules.md\n"
                "See docs/lessons/README.md for more information."
            )

        # Configured directories from gptme.toml
        config = get_config()
        if config.project and config.project.lessons.dirs:
            for dir_str in config.project.lessons.dirs:
                lesson_dir = Path(dir_str)
                # Make relative paths relative to config file location or cwd
                if not lesson_dir.is_absolute():
                    lesson_dir = Path.cwd() / lesson_dir
                if lesson_dir.exists():
                    dirs.append(lesson_dir)

        return dirs

    def _index_lessons(self) -> None:
        """Discover and parse all lessons."""
        self.lessons = []

        for lesson_dir in self.lesson_dirs:
            if not lesson_dir.exists():
                logger.debug(f"Lesson directory not found: {lesson_dir}")
                continue

            self._index_directory(lesson_dir)

        if self.lessons:
            logger.info(f"Indexed {len(self.lessons)} lessons")
        else:
            logger.debug("No lessons found")

    def _index_directory(self, directory: Path) -> None:
        """Index all lessons in a directory."""
        for md_file in directory.rglob("*.md"):
            # Skip special files
            if md_file.name.lower() in ("readme.md", "todo.md"):
                continue
            if "template" in md_file.name.lower():
                continue

            try:
                lesson = parse_lesson(md_file)
                # Filter based on status - only include active lessons
                if lesson.metadata.status != "active":
                    logger.debug(
                        f"Skipping {lesson.metadata.status} lesson: {md_file.relative_to(directory)}"
                    )
                    continue
                self.lessons.append(lesson)
            except Exception as e:
                logger.warning(f"Failed to parse lesson {md_file}: {e}")

    def search(self, query: str) -> list[Lesson]:
        """Search lessons by keyword or content.

        Args:
            query: Search query (case-insensitive)

        Returns:
            List of matching lessons
        """
        query_lower = query.lower()
        results = []

        for lesson in self.lessons:
            # Check title
            if query_lower in lesson.title.lower():
                results.append(lesson)
                continue

            # Check description
            if query_lower in lesson.description.lower():
                results.append(lesson)
                continue

            # Check keywords
            if any(query_lower in kw.lower() for kw in lesson.metadata.keywords):
                results.append(lesson)
                continue

        return results

    def find_by_keywords(self, keywords: list[str]) -> list[Lesson]:
        """Find lessons matching any of the given keywords.

        Args:
            keywords: List of keywords to match

        Returns:
            List of matching lessons
        """
        results = []

        for lesson in self.lessons:
            if any(kw in lesson.metadata.keywords for kw in keywords):
                results.append(lesson)

        return results

    def get_by_category(self, category: str) -> list[Lesson]:
        """Get all lessons in a category.

        Args:
            category: Category name (e.g., "tools", "patterns")

        Returns:
            List of lessons in category
        """
        return [lesson for lesson in self.lessons if lesson.category == category]

    def refresh(self) -> None:
        """Refresh the index by re-parsing all lessons."""
        self._index_lessons()
