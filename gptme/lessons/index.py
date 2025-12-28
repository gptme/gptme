"""Lesson index for discovery and search."""

import logging
import os
from pathlib import Path

from .parser import Lesson, parse_lesson

logger = logging.getLogger(__name__)

# Module-level cache for parsed lessons
# Key: lesson file path, Value: (mtime, parsed_lesson)
_LESSON_CACHE: dict[Path, tuple[float, Lesson]] = {}


def _get_cached_lesson(path: Path) -> Lesson | None:
    """Get cached lesson if still valid.

    Args:
        path: Path to lesson file

    Returns:
        Cached lesson if valid, None otherwise
    """
    if path not in _LESSON_CACHE:
        return None

    cached_mtime, cached_lesson = _LESSON_CACHE[path]

    try:
        current_mtime = path.stat().st_mtime
        if current_mtime == cached_mtime:
            logger.debug(f"Cache hit: {path.name}")
            return cached_lesson
        else:
            logger.debug(f"Cache invalidated (mtime changed): {path.name}")
    except FileNotFoundError:
        # File was deleted, remove from cache
        logger.debug(f"Cache invalidated (file deleted): {path.name}")
        del _LESSON_CACHE[path]

    return None


def _cache_lesson(path: Path, lesson: Lesson) -> None:
    """Cache a parsed lesson.

    Args:
        path: Path to lesson file
        lesson: Parsed lesson
    """
    try:
        mtime = path.stat().st_mtime
        _LESSON_CACHE[path] = (mtime, lesson)
        logger.debug(f"Cached lesson: {path.name}")
    except FileNotFoundError:
        logger.warning(f"Cannot cache lesson, file not found: {path}")


def clear_cache() -> None:
    """Clear the lesson cache.

    Useful for testing or when lessons are known to have changed.
    """
    global _LESSON_CACHE
    cache_size = len(_LESSON_CACHE)
    _LESSON_CACHE.clear()
    logger.info(f"Cleared lesson cache ({cache_size} entries)")


def get_cache_stats() -> dict[str, int]:
    """Get cache statistics.

    Returns:
        Dictionary with cache statistics
    """
    return {
        "cached_lessons": len(_LESSON_CACHE),
    }


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

        # Cursor rules directory (.cursor/)
        cursor_dir = Path.cwd() / ".cursor"
        if cursor_dir.exists():
            dirs.append(cursor_dir)
            logger.info(
                "Found .cursor directory with Cursor rules.\n"
                "gptme can now read and use .mdc rules files directly!\n"
                "Cursor 'globs' will be translated to gptme 'keywords'."
            )

        # Check for legacy .cursorrules file and provide guidance
        cursorrules_file = Path.cwd() / ".cursorrules"
        if cursorrules_file.exists():
            logger.info(
                "Found .cursorrules file in project root.\n"
                "Consider migrating to .cursor/ directory with .mdc files for better organization.\n"
                "gptme now supports reading Cursor .mdc rules directly!"
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

        # Plugin lesson directories (auto-discovered from [plugins].paths)
        if config.project and config.project.plugins.paths:
            from ..plugins import discover_plugins

            plugin_paths = [
                Path(p).expanduser().resolve() for p in config.project.plugins.paths
            ]
            plugins = discover_plugins(plugin_paths)
            for plugin in plugins:
                # Check for lessons/ directory in plugin
                # For flat layout: lessons/ is directly in plugin.path
                # For src/ layout: plugin.path is src/module/, so lessons/ is at project root
                plugin_lessons_dir = plugin.path / "lessons"
                if plugin_lessons_dir.is_dir():
                    dirs.append(plugin_lessons_dir)
                    logger.debug(
                        f"Found plugin lessons: {plugin.name} -> {plugin_lessons_dir}"
                    )
                else:
                    # Check for src/ layout: walk up to find project root
                    # plugin.path might be /project/src/module/, lessons at /project/lessons/
                    project_root = plugin.path.parent.parent
                    if (project_root / "pyproject.toml").exists():
                        project_lessons_dir = project_root / "lessons"
                        if project_lessons_dir.is_dir():
                            dirs.append(project_lessons_dir)
                            logger.debug(
                                f"Found plugin lessons (src layout): {plugin.name} -> {project_lessons_dir}"
                            )

        return dirs

    def _index_lessons(self) -> None:
        """Discover and parse all lessons (with caching and deduplication).

        Deduplication: Lessons are deduplicated by resolved path (realpath).
        This handles:
        - Symlinks pointing to files in other configured directories
        - Multiple paths resolving to the same physical file
        - Same filename in different directories (only first is used)

        Directory order determines precedence:
        1. User config (~/.config/gptme/lessons)
        2. Workspace (./lessons)
        3. Configured dirs (from gptme.toml)
        """
        self.lessons = []
        cache_hits = 0
        cache_misses = 0
        skipped_duplicates = 0
        # Track seen lesson paths (resolved via realpath) for deduplication
        # This handles symlinks pointing to the same file
        seen_paths: set[str] = set()

        for lesson_dir in self.lesson_dirs:
            if not lesson_dir.exists():
                logger.debug(f"Lesson directory not found: {lesson_dir}")
                continue

            hits, misses, skipped = self._index_directory(lesson_dir, seen_paths)
            cache_hits += hits
            cache_misses += misses
            skipped_duplicates += skipped

        log_parts = [f"Indexed {len(self.lessons)} lessons"]
        log_parts.append(f"(cache: {cache_hits} hits, {cache_misses} misses)")
        if skipped_duplicates > 0:
            log_parts.append(f"(skipped {skipped_duplicates} duplicates)")
        logger.info(" ".join(log_parts))

    def _index_directory(
        self, directory: Path, seen_paths: set[str]
    ) -> tuple[int, int, int]:
        """Index all lessons in a directory (with caching and deduplication).

        Args:
            directory: Directory to scan for lessons
            seen_paths: Set of resolved lesson paths already indexed (for deduplication)

        Returns:
            Tuple of (cache_hits, cache_misses, skipped_duplicates)
        """
        cache_hits = 0
        cache_misses = 0
        skipped_duplicates = 0

        # Find both .md and .mdc files
        lesson_files = list(directory.rglob("*.md")) + list(directory.rglob("*.mdc"))

        for lesson_file in lesson_files:
            # Skip special files
            if lesson_file.name.lower() in ("readme.md", "todo.md"):
                continue
            if "template" in lesson_file.name.lower():
                continue

            # Deduplication: Skip if lesson with same resolved path already indexed
            # This handles symlinks pointing to the same file
            resolved_path = os.path.realpath(lesson_file)
            if resolved_path in seen_paths:
                logger.debug(
                    f"Skipping duplicate lesson: {lesson_file.relative_to(directory)} "
                    f"(resolves to already indexed file)"
                )
                skipped_duplicates += 1
                continue

            try:
                # Try to use cached lesson first
                lesson = _get_cached_lesson(lesson_file)

                if lesson is None:
                    # Cache miss or invalid, parse and cache
                    lesson = parse_lesson(lesson_file)
                    _cache_lesson(lesson_file, lesson)
                    cache_misses += 1
                else:
                    cache_hits += 1

                # Filter based on status - only include active lessons
                if lesson.metadata.status != "active":
                    logger.debug(
                        f"Skipping {lesson.metadata.status} lesson: {lesson_file.relative_to(directory)}"
                    )
                    continue

                self.lessons.append(lesson)
                seen_paths.add(resolved_path)  # Mark resolved path as seen
            except Exception as e:
                logger.warning(f"Failed to parse lesson {lesson_file}: {e}")

        return cache_hits, cache_misses, skipped_duplicates

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
