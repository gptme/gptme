"""Tests for lesson parser."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
from gptme.lessons.parser import (
    Lesson,
    LessonMetadata,
    _extract_title,
    _extract_description,
    parse_lesson,
)


class TestExtractTitle:
    """Tests for _extract_title function."""

    def test_extract_title_with_h1(self):
        """Test extracting title from H1 heading."""
        content = "# My Lesson Title\n\nContent here"
        assert _extract_title(content) == "My Lesson Title"

    def test_extract_title_with_extra_spaces(self):
        """Test extracting title with extra whitespace."""
        content = "#   My Lesson Title   \n\nContent"
        assert _extract_title(content) == "My Lesson Title"

    def test_extract_title_multiple_headings(self):
        """Test extracting title when multiple headings present."""
        content = "# First Title\n\n## Second Heading\n\n# Third Title"
        # Should return first H1
        assert _extract_title(content) == "First Title"

    def test_extract_title_no_h1(self):
        """Test extracting title when no H1 present."""
        content = "## Only H2\n\nContent here"
        assert _extract_title(content) == "Untitled"

    def test_extract_title_empty_content(self):
        """Test extracting title from empty content."""
        assert _extract_title("") == "Untitled"

    def test_extract_title_h1_in_middle(self):
        """Test extracting title when H1 is not at start."""
        content = "Some text\n\n# My Title\n\nMore text"
        assert _extract_title(content) == "My Title"


class TestExtractDescription:
    """Tests for _extract_description function."""

    def test_extract_description_simple(self):
        """Test extracting simple description."""
        content = "# Title\n\nThis is the description.\n\nMore content"
        assert _extract_description(content) == "This is the description."

    def test_extract_description_with_multiple_paragraphs(self):
        """Test that only first paragraph is extracted."""
        content = "# Title\n\nFirst paragraph.\n\nSecond paragraph."
        assert _extract_description(content) == "First paragraph."

    def test_extract_description_skip_empty_lines(self):
        """Test that empty lines after title are skipped."""
        content = "# Title\n\n\n\nDescription here."
        assert _extract_description(content) == "Description here."

    def test_extract_description_no_title(self):
        """Test extracting description when no title present."""
        content = "Just some content without title"
        assert _extract_description(content) == ""

    def test_extract_description_only_title(self):
        """Test extracting description when only title present."""
        content = "# Title\n\n"
        assert _extract_description(content) == ""

    def test_extract_description_skip_h2(self):
        """Test that H2 headings are not returned as description."""
        content = "# Title\n\n## Subtitle\n\nActual description."
        assert _extract_description(content) == "Actual description."

    def test_extract_description_empty_content(self):
        """Test extracting description from empty content."""
        assert _extract_description("") == ""


class TestParseLesson:
    """Tests for parse_lesson function."""

    def test_parse_lesson_file_not_found(self):
        """Test parsing non-existent file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            parse_lesson(Path("/nonexistent/lesson.md"))
        assert "Lesson file not found" in str(exc_info.value)

    def test_parse_lesson_without_frontmatter(self):
        """Test parsing lesson without YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """# Test Lesson

This is a test description.

## Content

Some lesson content here.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Test Lesson"
            assert lesson.description == "This is a test description."
            assert lesson.category == "tools"
            assert lesson.metadata.keywords == []
            assert lesson.path == lesson_file
            assert "# Test Lesson" in lesson.body

    def test_parse_lesson_with_frontmatter(self):
        """Test parsing lesson with YAML frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "patterns"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """---
match:
  keywords: [test, sample, example]
---

# Test Lesson

This is a test description.

Lesson content.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Test Lesson"
            assert lesson.description == "This is a test description."
            assert lesson.category == "patterns"
            assert lesson.metadata.keywords == ["test", "sample", "example"]
            assert lesson.path == lesson_file
            assert "---" not in lesson.body  # Frontmatter stripped
            assert "# Test Lesson" in lesson.body

    def test_parse_lesson_frontmatter_without_match(self):
        """Test parsing lesson with frontmatter but no match section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """---
other_field: value
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Test Lesson"
            assert lesson.metadata.keywords == []

    def test_parse_lesson_empty_frontmatter(self):
        """Test parsing lesson with empty frontmatter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """---
---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Test Lesson"
            assert lesson.metadata.keywords == []

    def test_parse_lesson_invalid_yaml(self):
        """Test parsing lesson with invalid YAML raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """---
invalid: yaml: syntax:
---

# Test Lesson
"""
            lesson_file.write_text(content)

            with pytest.raises(ValueError) as exc_info:
                parse_lesson(lesson_file)
            assert "Invalid YAML frontmatter" in str(exc_info.value)

    def test_parse_lesson_without_yaml_module(self):
        """Test parsing with frontmatter when PyYAML not available."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test-lesson.md"

            content = """---
match:
  keywords: [test]
---

# Test Lesson
"""
            lesson_file.write_text(content)

            with patch("gptme.lessons.parser.HAS_YAML", False):
                with pytest.raises(ImportError) as exc_info:
                    parse_lesson(lesson_file)
                assert "PyYAML is required" in str(exc_info.value)

    def test_parse_lesson_minimal_content(self):
        """Test parsing lesson with minimal content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "minimal.md"

            content = "Some content"
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Untitled"
            assert lesson.description == ""
            assert lesson.category == "tools"

    def test_parse_lesson_category_from_parent(self):
        """Test that category is extracted from parent directory name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "custom-category"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test.md"

            content = "# Test\n\nDescription"
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.category == "custom-category"

    def test_parse_lesson_complex_frontmatter(self):
        """Test parsing lesson with complex frontmatter structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test.md"

            content = """---
match:
  keywords:
    - keyword1
    - keyword2
    - keyword3
other_section:
  field: value
---

# Complex Lesson

Description here.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            assert lesson.title == "Complex Lesson"
            assert len(lesson.metadata.keywords) == 3
            assert "keyword1" in lesson.metadata.keywords

    def test_parse_lesson_unicode_content(self):
        """Test parsing lesson with Unicode characters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "unicode.md"

            content = """# 测试课程 (Test Lesson)

Description with émojis 🚀 and spëcial çhars.

Content with unicode: café, naïve, Zürich.
"""
            lesson_file.write_text(content, encoding="utf-8")

            lesson = parse_lesson(lesson_file)

            assert "测试课程" in lesson.title
            assert "émojis" in lesson.description
            assert "Zürich" in lesson.body

    def test_parse_lesson_malformed_frontmatter_delimiter(self):
        """Test parsing with incomplete frontmatter delimiters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir) / "tools"
            lesson_dir.mkdir()
            lesson_file = lesson_dir / "test.md"

            # Only one delimiter, should treat as regular content
            content = """---

# Test Lesson

Description.
"""
            lesson_file.write_text(content)

            lesson = parse_lesson(lesson_file)

            # Should parse as content without frontmatter
            assert lesson.title == "Test Lesson"
            assert lesson.metadata.keywords == []


class TestLessonDataclasses:
    """Tests for Lesson and LessonMetadata dataclasses."""

    def test_lesson_metadata_default_keywords(self):
        """Test LessonMetadata with default empty keywords."""
        metadata = LessonMetadata()
        assert metadata.keywords == []
        assert isinstance(metadata.keywords, list)

    def test_lesson_metadata_with_keywords(self):
        """Test LessonMetadata with provided keywords."""
        metadata = LessonMetadata(keywords=["test", "sample"])
        assert metadata.keywords == ["test", "sample"]

    def test_lesson_dataclass_creation(self):
        """Test creating Lesson dataclass."""
        metadata = LessonMetadata(keywords=["test"])
        lesson = Lesson(
            path=Path("/test/lesson.md"),
            metadata=metadata,
            title="Test",
            description="Description",
            category="tools",
            body="Body content",
        )

        assert lesson.title == "Test"
        assert lesson.description == "Description"
        assert lesson.category == "tools"
        assert lesson.body == "Body content"
        assert lesson.metadata.keywords == ["test"]
        assert lesson.path == Path("/test/lesson.md")
