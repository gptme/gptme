"""Tests for lesson status filtering."""

import tempfile
from pathlib import Path

from gptme.lessons.index import LessonIndex
from gptme.lessons.parser import LessonMetadata, parse_lesson


class TestLessonStatus:
    """Tests for lesson status field in parser."""

    def test_status_default_active(self):
        """Test that lessons without status field default to 'active'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """---
match:
  keywords: [test]
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "active"

    def test_status_explicit_active(self):
        """Test lesson with explicit 'active' status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """---
status: active
match:
  keywords: [test]
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "active"

    def test_status_automated(self):
        """Test lesson with 'automated' status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """---
status: automated
automated_by: scripts/context.sh
match:
  keywords: [test]
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "automated"

    def test_status_deprecated(self):
        """Test lesson with 'deprecated' status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """---
status: deprecated
deprecated_by: lessons/new-approach.md
match:
  keywords: [test]
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "deprecated"

    def test_status_archived(self):
        """Test lesson with 'archived' status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """---
status: archived
archived_reason: "No longer relevant"
match:
  keywords: [test]
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "archived"

    def test_status_no_frontmatter(self):
        """Test lesson without frontmatter defaults to active."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            lesson_file = lesson_dir / "test.md"

            content = """# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)
            assert lesson.metadata.status == "active"


class TestLessonStatusFiltering:
    """Tests for status-based lesson filtering in index."""

    def test_index_includes_active_lessons(self):
        """Test that index includes lessons with active status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)

            # Create active lesson
            active_lesson = lesson_dir / "active.md"
            active_lesson.write_text("""---
status: active
match:
  keywords: [test]
---

# Active Lesson

Description.
""")

            index = LessonIndex(lesson_dirs=[lesson_dir])
            assert len(index.lessons) == 1
            assert index.lessons[0].title == "Active Lesson"

    def test_index_excludes_automated_lessons(self):
        """Test that index excludes lessons with automated status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)

            # Create automated lesson
            automated_lesson = lesson_dir / "automated.md"
            automated_lesson.write_text("""---
status: automated
match:
  keywords: [test]
---

# Automated Lesson

Description.
""")

            index = LessonIndex(lesson_dirs=[lesson_dir])
            assert len(index.lessons) == 0

    def test_index_excludes_deprecated_lessons(self):
        """Test that index excludes lessons with deprecated status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)

            # Create deprecated lesson
            deprecated_lesson = lesson_dir / "deprecated.md"
            deprecated_lesson.write_text("""---
status: deprecated
match:
  keywords: [test]
---

# Deprecated Lesson

Description.
""")

            index = LessonIndex(lesson_dirs=[lesson_dir])
            assert len(index.lessons) == 0

    def test_index_excludes_archived_lessons(self):
        """Test that index excludes lessons with archived status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)

            # Create archived lesson
            archived_lesson = lesson_dir / "archived.md"
            archived_lesson.write_text("""---
status: archived
match:
  keywords: [test]
---

# Archived Lesson

Description.
""")

            index = LessonIndex(lesson_dirs=[lesson_dir])
            assert len(index.lessons) == 0

    def test_index_mixed_status_lessons(self):
        """Test index with mix of active and non-active lessons."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)

            # Create multiple lessons with different statuses
            lessons_data = [
                ("active1.md", "active", "Active Lesson 1"),
                ("active2.md", "active", "Active Lesson 2"),
                ("automated.md", "automated", "Automated Lesson"),
                ("deprecated.md", "deprecated", "Deprecated Lesson"),
                ("archived.md", "archived", "Archived Lesson"),
                ("default.md", None, "Default Lesson"),  # No status = active
            ]

            for filename, status, title in lessons_data:
                status_line = f"status: {status}\n" if status else ""
                lesson_file = lesson_dir / filename
                lesson_file.write_text(f"""---
{status_line}match:
  keywords: [test]
---

# {title}

Description.
""")

            index = LessonIndex(lesson_dirs=[lesson_dir])

            # Should only include 3 active lessons (2 explicit + 1 default)
            assert len(index.lessons) == 3

            titles = {lesson.title for lesson in index.lessons}
            assert titles == {"Active Lesson 1", "Active Lesson 2", "Default Lesson"}


class TestLessonMetadataStatus:
    """Tests for LessonMetadata status field."""

    def test_metadata_default_status(self):
        """Test that LessonMetadata defaults to 'active' status."""
        metadata = LessonMetadata()
        assert metadata.status == "active"

    def test_metadata_explicit_status(self):
        """Test LessonMetadata with explicit status."""
        metadata = LessonMetadata(
            keywords=["test"], tools=["shell"], status="automated"
        )
        assert metadata.status == "automated"
        assert metadata.keywords == ["test"]
        assert metadata.tools == ["shell"]
