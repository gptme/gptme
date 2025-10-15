"""Lesson index for discovery and search."""

import logging
from pathlib import Path

from .cache import FileCache, get_file_cache
from .parser import Lesson, parse_lesson

logger = logging.getLogger(__name__)


class LessonIndex:
    """Index of available lessons with search and caching capabilities."""

    def __init__(self, lesson_dirs: list[Path] | None = None, use_cache: bool = True):
        """Initialize lesson index.

        Args:
            lesson_dirs: Directories to search for lessons.
                         If None, uses default locations.
            use_cache: Whether to use file caching (default: True)
        """
        self.lesson_dirs = lesson_dirs or self._default_dirs()
        self.lessons: list[Lesson] = []
        self.use_cache = use_cache

        # Initialize file cache
        self.file_cache: FileCache | None
        if use_cache:
            cache_dir = Path.home() / ".cache" / "gptme" / "lessons"
            self.file_cache = get_file_cache(cache_dir)
        else:
            self.file_cache = None

        self._index_lessons()

    @staticmethod
    def _default_dirs() -> list[Path]:
        """Get default lesson directories."""
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
        """Discover and parse all lessons with caching."""
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
        """Index all lessons in a directory with smart caching."""
        for md_file in directory.rglob("*.md"):
            # Skip special files
            if md_file.name.lower() in ("readme.md", "todo.md"):
                continue
            if "template" in md_file.name.lower():
                continue

            try:
                # Try cache first
                lesson = None
                if self.file_cache:
                    lesson = self.file_cache.get(md_file)
                    if lesson:
                        logger.debug(f"Cache hit: {md_file.relative_to(directory)}")

                # Parse if not cached or cache miss
                if lesson is None:
                    lesson = parse_lesson(md_file)
                    logger.debug(f"Parsed: {md_file.relative_to(directory)}")

                    # Cache the parsed lesson
                    if self.file_cache:
                        self.file_cache.set(md_file, lesson)

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
        """Refresh the index by checking for changes.

        Uses smart caching - only reparses files that have changed.
        Much faster than full reindex for incremental updates.
        """
        # Clear current lessons
        old_count = len(self.lessons)
        self.lessons = []

        # Re-index (will use cache for unchanged files)
        for lesson_dir in self.lesson_dirs:
            if not lesson_dir.exists():
                continue
            self._index_directory(lesson_dir)

        new_count = len(self.lessons)

        # Log refresh results
        if self.file_cache:
            logger.info(
                f"Refreshed index: {old_count} -> {new_count} lessons (using cache)"
            )
        else:
            logger.info(f"Refreshed index: {old_count} -> {new_count} lessons")

    def clear_cache(self) -> None:
        """Clear the persistent cache.

        Useful when lessons have been reorganized or cache is corrupted.
        """
        if self.file_cache:
            self.file_cache.clear()
            logger.info("Cleared lesson cache")
