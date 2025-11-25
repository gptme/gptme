"""Tests for lesson parser."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gptme.lessons.parser import (
    Lesson,
    LessonMetadata,
    _extract_description,
    _extract_title,
    _glob_to_keywords,
    _translate_cursor_metadata,
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


class TestGlobToKeywords:
    """Tests for _glob_to_keywords function."""

    def test_python_glob(self):
        """Test converting Python glob to keywords."""
        keywords = _glob_to_keywords("**/*.py")
        assert "python" in keywords
        assert "python code" in keywords

    def test_typescript_glob(self):
        """Test converting TypeScript glob to keywords."""
        keywords = _glob_to_keywords("**/*.ts")
        assert "typescript" in keywords
        assert "typescript code" in keywords

    def test_glob_with_directory_context(self):
        """Test glob with directory names for context."""
        keywords = _glob_to_keywords("src/api/**/*.js")
        assert "javascript" in keywords
        assert "api" in keywords

    def test_react_tsx_glob(self):
        """Test converting React TSX glob to keywords."""
        keywords = _glob_to_keywords("**/*.tsx")
        assert "typescript" in keywords
        assert "react" in keywords
        assert "frontend" in keywords

    def test_shell_script_glob(self):
        """Test converting shell script glob to keywords."""
        keywords = _glob_to_keywords("scripts/**/*.sh")
        assert "shell" in keywords or "bash" in keywords

    def test_markdown_glob(self):
        """Test converting markdown glob to keywords."""
        keywords = _glob_to_keywords("docs/**/*.md")
        assert "markdown" in keywords
        assert "documentation" in keywords

    def test_no_extension_glob(self):
        """Test glob without clear extension."""
        keywords = _glob_to_keywords("src/**/*")
        # Should still extract directory context
        assert "src" in keywords or keywords == []


class TestTranslateCursorMetadata:
    """Tests for _translate_cursor_metadata function."""

    def test_basic_cursor_metadata(self):
        """Test translating basic Cursor metadata."""
        frontmatter = {
            "name": "Python Style",
            "description": "Enforce PEP8",
            "globs": ["**/*.py"],
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        assert metadata.name == "Python Style"
        assert metadata.description == "Enforce PEP8"
        assert "python" in metadata.keywords
        assert metadata.globs == ["**/*.py"]

    def test_cursor_metadata_with_priority(self):
        """Test translating Cursor metadata with priority."""
        frontmatter = {
            "name": "High Priority Rule",
            "description": "Critical rule",
            "globs": ["**/*.ts"],
            "priority": "high",
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        assert metadata.priority == "high"
        assert "typescript" in metadata.keywords

    def test_cursor_metadata_with_always_apply(self):
        """Test translating Cursor metadata with alwaysApply."""
        frontmatter = {
            "name": "Global Rule",
            "description": "Applies everywhere",
            "alwaysApply": True,
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        assert metadata.always_apply is True
        # Should add high-frequency keywords
        assert "code" in metadata.keywords or "development" in metadata.keywords

    def test_cursor_metadata_with_triggers(self):
        """Test translating Cursor metadata with triggers."""
        frontmatter = {
            "name": "File Change Rule",
            "description": "Triggered on file change",
            "triggers": ["file_change", "build_error"],
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        assert metadata.triggers == ["file_change", "build_error"]

    def test_cursor_metadata_with_version(self):
        """Test translating Cursor metadata with version."""
        frontmatter = {
            "name": "Versioned Rule",
            "description": "Has version",
            "version": "1.0.0",
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        assert metadata.version == "1.0.0"

    def test_cursor_metadata_multiple_globs(self):
        """Test translating multiple globs to keywords."""
        frontmatter = {
            "name": "Multi-Language",
            "description": "Multiple file types",
            "globs": ["**/*.py", "**/*.ts", "**/*.js"],
        }
        metadata = _translate_cursor_metadata(frontmatter)
        
        # Should have keywords from all globs
        assert "python" in metadata.keywords
        assert "typescript" in metadata.keywords
        assert "javascript" in metadata.keywords


class TestParseMdcLesson:
    """Tests for parsing .mdc (Cursor rules) files."""

    def test_parse_basic_mdc_file(self):
        """Test parsing a basic .mdc file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            mdc_file = lesson_dir / "python-style.mdc"
            
            content = """---
name: Python Style
description: Enforce PEP8 compliance
globs: ["**/*.py"]
priority: high
---

# Python Style Guide

Enforce PEP8 style guidelines for all Python code.

## Rules

- Use 4 spaces for indentation
- Maximum line length of 88 characters
- Use type hints
"""
            mdc_file.write_text(content)
            
            lesson = parse_lesson(mdc_file)
            
            assert lesson.title == "Python Style Guide"
            assert lesson.metadata.name == "Python Style"
            assert lesson.metadata.description == "Enforce PEP8 compliance"
            assert "python" in lesson.metadata.keywords
            assert lesson.metadata.globs == ["**/*.py"]
            assert lesson.metadata.priority == "high"

    def test_parse_mdc_with_always_apply(self):
        """Test parsing .mdc file with alwaysApply."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            mdc_file = lesson_dir / "global-rule.mdc"
            
            content = """---
name: Global Standards
description: Project-wide conventions
alwaysApply: true
---

# Global Standards

Apply these conventions everywhere.
"""
            mdc_file.write_text(content)
            
            lesson = parse_lesson(mdc_file)
            
            assert lesson.metadata.always_apply is True
            # Should have high-frequency keywords
            assert len(lesson.metadata.keywords) > 0

    def test_parse_mdc_with_triggers(self):
        """Test parsing .mdc file with triggers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            mdc_file = lesson_dir / "on-change.mdc"
            
            content = """---
name: File Change Handler
description: Rules for file changes
triggers: ["file_change", "save"]
globs: ["**/*.ts"]
---

# File Change Handler

Handle file changes appropriately.
"""
            mdc_file.write_text(content)
            
            lesson = parse_lesson(mdc_file)
            
            assert lesson.metadata.triggers == ["file_change", "save"]
            assert "typescript" in lesson.metadata.keywords

    def test_parse_mdc_with_multiple_globs(self):
        """Test parsing .mdc file with multiple glob patterns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            mdc_file = lesson_dir / "multi-lang.mdc"
            
            content = """---
name: Multi-Language Standards
description: Standards for multiple languages
globs:
  - "**/*.py"
  - "**/*.ts"
  - "src/api/**/*.js"
---

# Multi-Language Standards

Standards that apply across languages.
"""
            mdc_file.write_text(content)
            
            lesson = parse_lesson(mdc_file)
            
            # Should have keywords from all globs
            keywords = lesson.metadata.keywords
            assert "python" in keywords
            assert "typescript" in keywords
            assert "javascript" in keywords
            assert "api" in keywords  # From directory context

    def test_parse_md_with_globs_field(self):
        """Test that .md files with globs field are treated as Cursor format."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            md_file = lesson_dir / "hybrid.md"
            
            # .md file but with Cursor-style globs field
            content = """---
name: Hybrid Format
description: MD file with Cursor fields
globs: ["**/*.py"]
---

# Hybrid Format

This should be treated as Cursor format.
"""
            md_file.write_text(content)
            
            lesson = parse_lesson(md_file)
            
            # Should be parsed as Cursor format
            assert lesson.metadata.globs == ["**/*.py"]
            assert "python" in lesson.metadata.keywords

    def test_parse_regular_md_lesson_still_works(self):
        """Test that regular .md lessons still parse correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lesson_dir = Path(tmpdir)
            md_file = lesson_dir / "regular.md"
            
            content = """---
match:
  keywords: [patch, editing]
status: active
---

# Regular Lesson

This is a standard gptme lesson.
"""
            md_file.write_text(content)
            
            lesson = parse_lesson(md_file)
            
            # Should parse as gptme format
            assert lesson.metadata.keywords == ["patch", "editing"]
            assert lesson.metadata.status == "active"
            assert lesson.metadata.globs == []  # Should not have globs
