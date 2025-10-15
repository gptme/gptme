"""Caching system for lesson parsing and matching.

Provides:
1. File-level caching: Cache parsed lessons with mtime tracking
2. Match result caching: LRU cache for match results
3. Smart refresh: Only reparse changed files
4. Persistent cache: Store across sessions (optional)
"""

import logging
import pickle
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .parser import Lesson

logger = logging.getLogger(__name__)


@dataclass
class CachedLesson:
    """Cached lesson with metadata."""

    lesson: Lesson
    mtime: float
    path: str


class FileCache:
    """File-level cache for parsed lessons with mtime tracking."""

    def __init__(self, cache_dir: Path | None = None):
        """Initialize file cache.

        Args:
            cache_dir: Directory for persistent cache. If None, uses memory only.
        """
        self.cache_dir = cache_dir
        self.memory_cache: dict[str, CachedLesson] = {}

        # Load persistent cache if available
        if cache_dir and cache_dir.exists():
            self._load_persistent_cache()

    def _load_persistent_cache(self) -> None:
        """Load lessons from persistent cache."""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "lesson_cache.pkl"
        if not cache_file.exists():
            return

        try:
            with open(cache_file, "rb") as f:
                self.memory_cache = pickle.load(f)
            logger.debug(f"Loaded {len(self.memory_cache)} lessons from cache")
        except Exception as e:
            logger.warning(f"Failed to load persistent cache: {e}")
            self.memory_cache = {}

    def _save_persistent_cache(self) -> None:
        """Save lessons to persistent cache."""
        if not self.cache_dir:
            return

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / "lesson_cache.pkl"

            with open(cache_file, "wb") as f:
                pickle.dump(self.memory_cache, f)

            logger.debug(f"Saved {len(self.memory_cache)} lessons to cache")
        except Exception as e:
            logger.warning(f"Failed to save persistent cache: {e}")

    def get(self, path: Path) -> Lesson | None:
        """Get cached lesson if still valid.

        Args:
            path: Lesson file path

        Returns:
            Cached lesson if valid, None if stale or not cached
        """
        path_str = str(path)

        # Check memory cache
        if path_str not in self.memory_cache:
            return None

        cached = self.memory_cache[path_str]

        # Check if file has been modified
        try:
            current_mtime = path.stat().st_mtime
            if current_mtime > cached.mtime:
                # File modified, invalidate
                del self.memory_cache[path_str]
                return None

            return cached.lesson
        except (OSError, FileNotFoundError):
            # File no longer exists
            del self.memory_cache[path_str]
            return None

    def set(self, path: Path, lesson: Lesson) -> None:
        """Cache a parsed lesson.

        Args:
            path: Lesson file path
            lesson: Parsed lesson
        """
        try:
            mtime = path.stat().st_mtime
            self.memory_cache[str(path)] = CachedLesson(
                lesson=lesson, mtime=mtime, path=str(path)
            )

            # Save to persistent cache if enabled
            if self.cache_dir:
                self._save_persistent_cache()

        except (OSError, FileNotFoundError):
            logger.warning(f"Failed to cache lesson {path}: file not found")

    def check_stale(self, path: Path) -> bool:
        """Check if cached lesson is stale.

        Args:
            path: Lesson file path

        Returns:
            True if stale (needs reparse), False if fresh
        """
        return self.get(path) is None

    def invalidate(self, path: Path) -> None:
        """Invalidate cached lesson.

        Args:
            path: Lesson file path
        """
        path_str = str(path)
        if path_str in self.memory_cache:
            del self.memory_cache[path_str]

            if self.cache_dir:
                self._save_persistent_cache()

    def clear(self) -> None:
        """Clear all cached lessons."""
        self.memory_cache.clear()

        if self.cache_dir:
            cache_file = self.cache_dir / "lesson_cache.pkl"
            if cache_file.exists():
                cache_file.unlink()


class MatchCache:
    """LRU cache for lesson match results."""

    def __init__(self, max_size: int = 100):
        """Initialize match cache.

        Args:
            max_size: Maximum number of cached match results
        """
        self.max_size = max_size
        self.cache: OrderedDict[str, Any] = OrderedDict()

    def _make_key(self, keywords: list[str], tools_used: list[str]) -> str:
        """Create cache key from match context.

        Args:
            keywords: Keywords from context
            tools_used: Tools used in context

        Returns:
            Cache key string
        """
        # Sort for consistent keys
        kw_str = "|".join(sorted(keywords))
        tools_str = "|".join(sorted(tools_used))
        return f"{kw_str}:::{tools_str}"

    def get(self, keywords: list[str], tools_used: list[str]) -> list[Any] | None:
        """Get cached match results.

        Args:
            keywords: Keywords from context
            tools_used: Tools used in context

        Returns:
            Cached match results or None
        """
        key = self._make_key(keywords, tools_used)

        if key in self.cache:
            # Move to end (most recently used)
            self.cache.move_to_end(key)
            return self.cache[key]

        return None

    def set(
        self, keywords: list[str], tools_used: list[str], results: list[Any]
    ) -> None:
        """Cache match results.

        Args:
            keywords: Keywords from context
            tools_used: Tools used in context
            results: Match results to cache
        """
        key = self._make_key(keywords, tools_used)

        # Add to cache
        self.cache[key] = results

        # Evict oldest if over limit
        if len(self.cache) > self.max_size:
            self.cache.popitem(last=False)  # Remove oldest (FIFO)

    def clear(self) -> None:
        """Clear all cached match results."""
        self.cache.clear()


# Global caches (thread-safe via GIL for simple operations)
_file_cache: FileCache | None = None
_match_cache: MatchCache | None = None


def get_file_cache(cache_dir: Path | None = None) -> FileCache:
    """Get global file cache instance.

    Args:
        cache_dir: Directory for persistent cache

    Returns:
        Global FileCache instance
    """
    global _file_cache
    if _file_cache is None:
        _file_cache = FileCache(cache_dir)
    return _file_cache


def get_match_cache(max_size: int = 100) -> MatchCache:
    """Get global match cache instance.

    Args:
        max_size: Maximum cache size

    Returns:
        Global MatchCache instance
    """
    global _match_cache
    if _match_cache is None:
        _match_cache = MatchCache(max_size)
    return _match_cache
