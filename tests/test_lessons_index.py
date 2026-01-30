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
    """Tests for lesson deduplication feature.

    Deduplication is based on resolved path (realpath), not filename.
    This handles symlinks pointing to the same file correctly.
    """

    def test_same_filename_different_files_not_deduplicated(
        self, sample_lesson_content
    ):
        """Test that different files with same filename are NOT deduplicated.

        With realpath-based deduplication, files with same name but different
        paths are treated as separate lessons.
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

            # Same filename in both directories, but different physical files
            content1 = sample_lesson_content.replace("Test Lesson", "First Version")
            content2 = sample_lesson_content.replace("Test Lesson", "Second Version")

            (dir1 / "duplicate.md").write_text(content1)
            (dir2 / "duplicate.md").write_text(content2)

            index = LessonIndex([dir1, dir2])

            # Both files have different realpaths, so both are included
            assert len(index.lessons) == 2
            titles = {lesson.title for lesson in index.lessons}
            assert "First Version" in titles
            assert "Second Version" in titles

    def test_symlink_deduplication(self, sample_lesson_content):
        """Test that symlinks to the same file are deduplicated.

        This is the main use case: when lessons/x.md is a symlink to
        gptme-contrib/lessons/x.md, and both directories are configured,
        the lesson should only be included once.
        """
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            real_dir = Path(tmp1) / "real-lessons"
            symlink_dir = Path(tmp2) / "symlinked-lessons"
            real_dir.mkdir()
            symlink_dir.mkdir()

            # Create the real file
            content = sample_lesson_content.replace("Test Lesson", "Real Lesson")
            real_file = real_dir / "shared-lesson.md"
            real_file.write_text(content)

            # Create a symlink pointing to the real file
            symlink_file = symlink_dir / "shared-lesson.md"
            symlink_file.symlink_to(real_file)

            # Index both directories (symlink dir first)
            index = LessonIndex([symlink_dir, real_dir])

            # Should only have one lesson (realpath deduplication)
            assert len(index.lessons) == 1
            assert index.lessons[0].title == "Real Lesson"

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

    def test_symlink_in_subdirectory(self, sample_lesson_content):
        """Test symlink deduplication works in subdirectories."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            real_dir = Path(tmp1) / "lessons"
            symlink_dir = Path(tmp2) / "lessons"
            real_dir.mkdir()
            symlink_dir.mkdir()

            # Create real file in subdirectory
            (real_dir / "workflow").mkdir()
            content = sample_lesson_content.replace("Test Lesson", "Workflow Lesson")
            real_file = real_dir / "workflow" / "git-workflow.md"
            real_file.write_text(content)

            # Create symlink in different subdirectory
            (symlink_dir / "tools").mkdir()
            symlink_file = symlink_dir / "tools" / "git-workflow.md"
            symlink_file.symlink_to(real_file)

            index = LessonIndex([symlink_dir, real_dir])

            # Should only have one lesson (symlink resolves to same file)
            assert len(index.lessons) == 1
            assert index.lessons[0].title == "Workflow Lesson"

    def test_different_files_same_name_in_subdirectories(self, sample_lesson_content):
        """Test different files with same name in subdirectories are NOT deduplicated."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
        ):
            dir1 = Path(tmp1) / "lessons"
            dir2 = Path(tmp2) / "lessons"
            dir1.mkdir()
            dir2.mkdir()

            # Create different files with same name in different subdirectories
            (dir1 / "workflow").mkdir()
            (dir2 / "tools").mkdir()

            content1 = sample_lesson_content.replace("Test Lesson", "Workflow Version")
            content2 = sample_lesson_content.replace("Test Lesson", "Tools Version")

            (dir1 / "workflow" / "git-workflow.md").write_text(content1)
            (dir2 / "tools" / "git-workflow.md").write_text(content2)

            index = LessonIndex([dir1, dir2])

            # Both files have different realpaths, so both are included
            assert len(index.lessons) == 2
            titles = {lesson.title for lesson in index.lessons}
            assert "Workflow Version" in titles
            assert "Tools Version" in titles

    def test_symlinks_multiple_directories(self, sample_lesson_content):
        """Test symlink deduplication with more than 2 directories."""
        clear_cache()
        with (
            tempfile.TemporaryDirectory() as tmp1,
            tempfile.TemporaryDirectory() as tmp2,
            tempfile.TemporaryDirectory() as tmp3,
        ):
            # tmp1 has the real file
            real_dir = Path(tmp1) / "lessons"
            real_dir.mkdir()
            content = sample_lesson_content.replace("Test Lesson", "Real Lesson")
            real_file = real_dir / "shared.md"
            real_file.write_text(content)

            # tmp2 and tmp3 have symlinks to the real file
            symlink_dirs = []
            for tmp in [tmp2, tmp3]:
                symlink_dir = Path(tmp) / "lessons"
                symlink_dir.mkdir()
                symlink_dirs.append(symlink_dir)
                symlink_file = symlink_dir / "shared.md"
                symlink_file.symlink_to(real_file)

            # Index all directories
            index = LessonIndex(symlink_dirs + [real_dir])

            # Should only have one lesson (all resolve to same realpath)
            assert len(index.lessons) == 1
            assert index.lessons[0].title == "Real Lesson"

    def test_different_files_multiple_directories(self, sample_lesson_content):
        """Test different files with same name in multiple directories."""
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

            # All files have different realpaths, so all are included
            assert len(index.lessons) == 3
            titles = {lesson.title for lesson in index.lessons}
            assert "Version 1" in titles
            assert "Version 2" in titles
            assert "Version 3" in titles


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


class TestWorktreeExclusion:
    """Test that lessons in worktree directories are excluded."""

    def test_worktree_lessons_excluded(self, tmp_path):
        """Lessons in worktree/ subdirectories should be excluded."""
        from gptme.lessons.index import LessonIndex

        # Create a normal lesson
        normal_lesson = tmp_path / "normal.md"
        normal_lesson.write_text(
            "---\nmatch:\n  keywords: [normal]\n---\n# Normal Lesson\n\nContent."
        )

        # Create a worktree subdirectory with a lesson
        worktree_dir = tmp_path / "worktree" / "some-branch" / "lessons"
        worktree_dir.mkdir(parents=True)
        worktree_lesson = worktree_dir / "worktree-lesson.md"
        worktree_lesson.write_text(
            "---\nmatch:\n  keywords: [worktree]\n---\n# Worktree Lesson\n\nContent."
        )

        # Index should only include the normal lesson, not the worktree one
        index = LessonIndex(lesson_dirs=[tmp_path])

        lesson_names = [lesson.title for lesson in index.lessons]
        assert "Normal Lesson" in lesson_names
        assert "Worktree Lesson" not in lesson_names
        assert len(index.lessons) == 1

    def test_git_directory_excluded(self, tmp_path):
        """Lessons in .git directories should be excluded."""
        from gptme.lessons.index import LessonIndex

        # Create a normal lesson
        normal_lesson = tmp_path / "normal.md"
        normal_lesson.write_text(
            "---\nmatch:\n  keywords: [normal]\n---\n# Normal Lesson\n\nContent."
        )

        # Create a .git subdirectory with a lesson (shouldn't happen but test it)
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        git_lesson = git_dir / "some-file.md"
        git_lesson.write_text(
            "---\nmatch:\n  keywords: [git]\n---\n# Git Lesson\n\nContent."
        )

        # Index should only include the normal lesson
        index = LessonIndex(lesson_dirs=[tmp_path])

        lesson_names = [lesson.title for lesson in index.lessons]
        assert "Normal Lesson" in lesson_names
        assert "Git Lesson" not in lesson_names
        assert len(index.lessons) == 1
