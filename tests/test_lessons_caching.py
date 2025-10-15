"""Tests for lesson caching system."""

import tempfile
from pathlib import Path
from time import sleep

import pytest

from gptme.lessons import LessonIndex, LessonMatcher, MatchContext
from gptme.lessons.cache import FileCache, MatchCache


@pytest.fixture
def temp_lesson_dir():
    """Create temporary directory with test lessons."""
    with tempfile.TemporaryDirectory() as tmpdir:
        lesson_dir = Path(tmpdir) / "lessons"
        lesson_dir.mkdir()

        # Create test lessons
        lesson1 = lesson_dir / "test1.md"
        lesson1.write_text(
            """---
match:
  keywords: [python, code]
---
# Test Lesson 1
Test content 1
"""
        )

        lesson2 = lesson_dir / "test2.md"
        lesson2.write_text(
            """---
match:
  keywords: [shell, bash]
---
# Test Lesson 2
Test content 2
"""
        )

        yield lesson_dir


def test_file_cache_basic(temp_lesson_dir):
    """Test basic file cache operations."""
    cache = FileCache()

    lesson_file = temp_lesson_dir / "test1.md"

    # Cache miss initially
    assert cache.get(lesson_file) is None

    # Parse and cache
    from gptme.lessons import parse_lesson

    lesson = parse_lesson(lesson_file)
    cache.set(lesson_file, lesson)

    # Cache hit
    cached = cache.get(lesson_file)
    assert cached is not None
    assert cached.title == lesson.title


def test_file_cache_mtime_invalidation(temp_lesson_dir):
    """Test that cache is invalidated when file is modified."""
    cache = FileCache()

    lesson_file = temp_lesson_dir / "test1.md"

    # Parse and cache
    from gptme.lessons import parse_lesson

    lesson = parse_lesson(lesson_file)
    cache.set(lesson_file, lesson)

    # Modify file (need to wait a bit to ensure mtime changes)
    sleep(0.01)
    lesson_file.write_text(
        """---
match:
  keywords: [python, modified]
---
# Modified Lesson
Modified content
"""
    )

    # Cache should be invalid
    assert cache.get(lesson_file) is None


def test_file_cache_persistent(temp_lesson_dir):
    """Test persistent cache across instances."""
    cache_dir = temp_lesson_dir / ".cache"
    cache_dir.mkdir()

    # First instance - populate cache
    cache1 = FileCache(cache_dir)
    lesson_file = temp_lesson_dir / "test1.md"

    from gptme.lessons import parse_lesson

    lesson = parse_lesson(lesson_file)
    cache1.set(lesson_file, lesson)

    # Second instance - should load from persistent cache
    cache2 = FileCache(cache_dir)
    cached = cache2.get(lesson_file)
    assert cached is not None
    assert cached.title == lesson.title


def test_match_cache_basic():
    """Test basic match cache operations."""
    cache = MatchCache(max_size=2)

    # Cache miss
    assert cache.get(["python"], ["shell"]) is None

    # Set and get
    results = ["result1", "result2"]
    cache.set(["python"], ["shell"], results)
    assert cache.get(["python"], ["shell"]) == results

    # Different keywords - cache miss
    assert cache.get(["rust"], ["shell"]) is None


def test_match_cache_lru_eviction():
    """Test LRU eviction in match cache."""
    cache = MatchCache(max_size=2)

    # Add 3 items (should evict first)
    cache.set(["a"], [], ["result_a"])
    cache.set(["b"], [], ["result_b"])
    cache.set(["c"], [], ["result_c"])

    # First item should be evicted
    assert cache.get(["a"], []) is None
    assert cache.get(["b"], []) == ["result_b"]
    assert cache.get(["c"], []) == ["result_c"]


def test_match_cache_key_normalization():
    """Test that match cache normalizes keyword order."""
    cache = MatchCache()

    results = ["result"]
    cache.set(["python", "code"], ["shell"], results)

    # Same keywords, different order - should hit cache
    assert cache.get(["code", "python"], ["shell"]) == results


def test_lesson_index_with_caching(temp_lesson_dir):
    """Test LessonIndex uses caching correctly."""
    # First load - should parse all files
    index1 = LessonIndex([temp_lesson_dir], use_cache=True)
    assert len(index1.lessons) == 2

    # Second load - should use cache (faster)
    index2 = LessonIndex([temp_lesson_dir], use_cache=True)
    assert len(index2.lessons) == 2

    # Verify cache was used by checking file cache
    lesson_file = temp_lesson_dir / "test1.md"
    assert index2.file_cache is not None
    assert index2.file_cache.get(lesson_file) is not None


def test_lesson_index_refresh_caching(temp_lesson_dir):
    """Test that refresh uses caching."""
    index = LessonIndex([temp_lesson_dir], use_cache=True)
    initial_count = len(index.lessons)

    # Add new lesson
    new_lesson = temp_lesson_dir / "test3.md"
    new_lesson.write_text(
        """---
match:
  keywords: [new, test]
---
# New Lesson
New content
"""
    )

    # Refresh - should detect new lesson
    index.refresh()
    assert len(index.lessons) == initial_count + 1


def test_lesson_matcher_with_caching(temp_lesson_dir):
    """Test LessonMatcher uses caching correctly."""
    index = LessonIndex([temp_lesson_dir], use_cache=False)
    matcher = LessonMatcher(use_cache=True)

    context = MatchContext(message="I need help with python code", tools_used=[])

    # First match - should compute
    results1 = matcher.match(index.lessons, context)
    assert len(results1) > 0

    # Second match - should use cache
    results2 = matcher.match(index.lessons, context)
    assert results2 == results1


def test_lesson_index_clear_cache(temp_lesson_dir):
    """Test clearing lesson cache."""
    cache_dir = temp_lesson_dir / ".cache"
    cache_dir.mkdir()

    index = LessonIndex([temp_lesson_dir], use_cache=True)
    assert len(index.lessons) == 2

    # Cache should exist
    lesson_file = temp_lesson_dir / "test1.md"
    assert index.file_cache is not None
    assert index.file_cache.get(lesson_file) is not None

    # Clear cache
    index.clear_cache()

    # Cache should be empty
    assert index.file_cache is not None
    assert index.file_cache.get(lesson_file) is None
