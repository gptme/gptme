"""Tests for lesson index module."""

import tempfile
from pathlib import Path

import pytest

from gptme.lessons.index import LessonIndex, clear_cache, get_cache_stats


@pytest.fixture
def temp_lesson_dir():
    """Create a temporary directory with test lessons."""
    with tempfile.TemporaryDirectory() as tmp:
        lesson_dir = Path(tmp) / "lessons"
        lesson_dir.mkdir()
        yield lesson_dir


@pytest.fixture
def sample_lesson_content():
    """Sample lesson content."""
    return """---
match:
  keywords: ["test", "sample"]
status: active
---

# Test Lesson

This is a test lesson for unit testing.

## Context
Testing the lesson index.
"""


class TestLessonIndex:
    """Tests for LessonIndex class."""

    def test_index_empty_directory(self, temp_lesson_dir):
        """Test indexing an empty directory."""
        clear_cache()
        index = LessonIndex([temp_lesson_dir])
        assert len(index.lessons) == 0

    def test_index_single_lesson(self, temp_lesson_dir, sample_lesson_content):
        """Test indexing a single lesson."""
        clear_cache()
        lesson_file = temp_lesson_dir / "test-lesson.md"
        lesson_file.write_text(sample_lesson_content)

        index = LessonIndex([temp_lesson_dir])
        assert len(index.lessons) == 1
        assert index.lessons[0].title == "Test Lesson"

    def test_index_multiple_lessons(self, temp_lesson_dir, sample_lesson_content):
        """Test indexing multiple lessons."""
        clear_cache()
        for i in range(3):
            lesson_file = temp_lesson_dir / f"lesson-{i}.md"
            content = sample_lesson_content.replace("Test Lesson", f"Lesson {i}")
            lesson_file.write_text(content)

        index = LessonIndex([temp_lesson_dir])
        assert len(index.lessons) == 3

    def test_index_skips_readme(self, temp_lesson_dir, sample_lesson_content):
        """Test that README.md is skipped."""
        clear_cache()
        # Create a regular lesson
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)
        # Create a README that should be skipped
        (temp_lesson_dir / "README.md").write_text("# Readme\n\nNot a lesson.")

        index = LessonIndex([temp_lesson_dir])
        assert len(index.lessons) == 1
        assert index.lessons[0].title == "Test Lesson"

    def test_index_skips_templates(self, temp_lesson_dir, sample_lesson_content):
        """Test that template files are skipped."""
        clear_cache()
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)
        (temp_lesson_dir / "template.md").write_text(sample_lesson_content)
        (temp_lesson_dir / "lesson-template.md").write_text(sample_lesson_content)

        index = LessonIndex([temp_lesson_dir])
        assert len(index.lessons) == 1


class TestLessonDeduplication:
    """Tests for lesson deduplication feature."""

    def test_deduplication_same_filename(self, sample_lesson_content):
        """Test that lessons with same filename are deduplicated.

        The first directory in the list should take precedence.
        """
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            dir1 = Path(tmp1) / "lessons"
            dir2 = Path(tmp2) / "lessons"
            dir1.mkdir()
            dir2.mkdir()

            # Same filename in both directories
            content1 = sample_lesson_content.replace("Test Lesson", "First Version")
            content2 = sample_lesson_content.replace("Test Lesson", "Second Version")

            (dir1 / "duplicate.md").write_text(content1)
            (dir2 / "duplicate.md").write_text(content2)

            # dir1 has higher precedence (first in list)
            index = LessonIndex([dir1, dir2])

            # Should only have one lesson
            assert len(index.lessons) == 1
            # Should be the first version (from dir1)
            assert index.lessons[0].title == "First Version"

    def test_deduplication_different_filenames(self, sample_lesson_content):
        """Test that lessons with different filenames are not deduplicated."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            dir1 = Path(tmp1) / "lessons"
            dir2 = Path(tmp2) / "lessons"
            dir1.mkdir()
            dir2.mkdir()

            content1 = sample_lesson_content.replace("Test Lesson", "Lesson A")
            content2 = sample_lesson_content.replace("Test Lesson", "Lesson B")

            (dir1 / "lesson-a.md").write_text(content1)
            (dir2 / "lesson-b.md").write_text(content2)

            index = LessonIndex([dir1, dir2])

            # Should have both lessons
            assert len(index.lessons) == 2
            titles = {lesson.title for lesson in index.lessons}
            assert "Lesson A" in titles
            assert "Lesson B" in titles

    def test_deduplication_in_subdirectories(self, sample_lesson_content):
        """Test deduplication works across subdirectories."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            dir1 = Path(tmp1) / "lessons"
            dir2 = Path(tmp2) / "lessons"
            dir1.mkdir()
            dir2.mkdir()

            # Create in different subdirectories but same filename
            (dir1 / "workflow").mkdir()
            (dir2 / "tools").mkdir()

            content1 = sample_lesson_content.replace("Test Lesson", "Workflow Version")
            content2 = sample_lesson_content.replace("Test Lesson", "Tools Version")

            (dir1 / "workflow" / "git-workflow.md").write_text(content1)
            (dir2 / "tools" / "git-workflow.md").write_text(content2)

            index = LessonIndex([dir1, dir2])

            # Should only have one lesson
            assert len(index.lessons) == 1
            # Should be from dir1 (workflow category)
            assert index.lessons[0].title == "Workflow Version"

    def test_deduplication_multiple_duplicates(self, sample_lesson_content):
        """Test deduplication with more than 2 directories."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
            tempfile.TemporaryDirectory() as tmp3,
        ):
            dirs = []
            for i, tmp in enumerate([tmp1, tmp2, tmp3]):
                lesson_dir = Path(tmp) / "lessons"
                lesson_dir.mkdir()
                dirs.append(lesson_dir)

                content = sample_lesson_content.replace(
                    "Test Lesson", f"Version {i + 1}"
                )
                (lesson_dir / "shared.md").write_text(content)

            index = LessonIndex(dirs)

            # Should only have one lesson
            assert len(index.lessons) == 1
            # Should be Version 1 (from first directory)
            assert index.lessons[0].title == "Version 1"


class TestLessonCache:
    """Tests for lesson caching functionality."""

    def test_cache_hit(self, temp_lesson_dir, sample_lesson_content):
        """Test that cached lessons are reused."""
        clear_cache()
        lesson_file = temp_lesson_dir / "cached.md"
        lesson_file.write_text(sample_lesson_content)

        # First index - should cache
        LessonIndex([temp_lesson_dir])
        initial_stats = get_cache_stats()
        assert initial_stats["cached_lessons"] >= 1

        # Second index - should hit cache
        LessonIndex([temp_lesson_dir])
        final_stats = get_cache_stats()
        assert final_stats["cached_lessons"] >= 1

    def test_cache_invalidation_on_change(self, temp_lesson_dir, sample_lesson_content):
        """Test that cache is invalidated when file changes."""
        clear_cache()
        lesson_file = temp_lesson_dir / "changing.md"
        lesson_file.write_text(sample_lesson_content)

        # First index
        index1 = LessonIndex([temp_lesson_dir])
        assert index1.lessons[0].title == "Test Lesson"

        # Modify file
        new_content = sample_lesson_content.replace("Test Lesson", "Modified Lesson")
        import time

        time.sleep(0.01)  # Ensure mtime changes
        lesson_file.write_text(new_content)

        # Second index should pick up changes
        index2 = LessonIndex([temp_lesson_dir])
        assert index2.lessons[0].title == "Modified Lesson"

    def test_clear_cache(self, temp_lesson_dir, sample_lesson_content):
        """Test cache clearing."""
        clear_cache()
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)

        LessonIndex([temp_lesson_dir])
        stats1 = get_cache_stats()
        assert stats1["cached_lessons"] >= 1

        clear_cache()
        stats2 = get_cache_stats()
        assert stats2["cached_lessons"] == 0


class TestLessonSearch:
    """Tests for lesson search functionality."""

    def test_search_by_title(self, temp_lesson_dir, sample_lesson_content):
        """Test searching lessons by title."""
        clear_cache()
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)

        index = LessonIndex([temp_lesson_dir])
        results = index.search("Test")

        assert len(results) == 1
        assert results[0].title == "Test Lesson"

    def test_search_no_results(self, temp_lesson_dir, sample_lesson_content):
        """Test search with no matching results."""
        clear_cache()
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)

        index = LessonIndex([temp_lesson_dir])
        results = index.search("nonexistent")

        assert len(results) == 0

    def test_search_case_insensitive(self, temp_lesson_dir, sample_lesson_content):
        """Test that search is case-insensitive."""
        clear_cache()
        (temp_lesson_dir / "test.md").write_text(sample_lesson_content)

        index = LessonIndex([temp_lesson_dir])
        results = index.search("test lesson")

        assert len(results) == 1
