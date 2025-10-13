"""Tests for lesson CLI commands."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from gptme.lessons.commands import (
    lesson,
    _lesson_help,
    _lesson_list,
    _lesson_search,
    _lesson_show,
    _lesson_refresh,
)
from gptme.lessons.parser import Lesson, LessonMetadata


# Sample test lessons
def create_test_lesson(
    title: str = "Test Lesson",
    category: str = "tools",
    description: str = "Test description",
    keywords: list[str] | None = None,
    body: str = "Test body content",
    path: Path | None = None,
) -> Lesson:
    """Create a test lesson for testing."""
    if keywords is None:
        keywords = ["test", "sample"]
    if path is None:
        path = Path("/fake/path/test-lesson.md")

    metadata = LessonMetadata(keywords=keywords)
    return Lesson(
        title=title,
        category=category,
        description=description,
        body=body,
        metadata=metadata,
        path=path,
    )


@pytest.fixture
def mock_lesson_index():
    """Create a mock LessonIndex with test lessons."""
    with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
        mock_index = MagicMock()

        # Create sample lessons
        lessons = [
            create_test_lesson(
                title="Patch Placeholders",
                category="tools",
                description="Avoiding undefined placeholders in patches",
                keywords=["patch", "placeholder", "error"],
                body="# Patch Placeholders\n\nRule: Never use undefined placeholders.",
                path=Path("/lessons/tools/patch-placeholders.md"),
            ),
            create_test_lesson(
                title="Browser Interaction",
                category="tools",
                description="Browser tool capabilities",
                keywords=["browser", "web", "interaction"],
                body="# Browser Interaction\n\nRule: Use browser tool for web tasks.",
                path=Path("/lessons/tools/browser-interaction.md"),
            ),
            create_test_lesson(
                title="Persistent Learning",
                category="patterns",
                description="Meta-learning patterns",
                keywords=["learning", "persist", "meta"],
                body="# Persistent Learning\n\nRule: Always persist insights.",
                path=Path("/lessons/patterns/persistent-learning.md"),
            ),
        ]

        mock_index.lessons = lessons
        mock_index.get_by_category = lambda cat: [
            lesson for lesson in lessons if lesson.category == cat
        ]

        def search_func(query):
            return [
                lesson
                for lesson in lessons
                if query.lower() in lesson.title.lower()
                or query.lower() in " ".join(lesson.metadata.keywords).lower()
            ]

        mock_index.search = search_func
        mock_index.refresh = MagicMock()

        mock_index_class.return_value = mock_index
        yield mock_index


class TestLessonHelp:
    """Tests for _lesson_help function."""

    def test_lesson_help_content(self):
        """Test that help text contains expected content."""
        help_text = _lesson_help()

        assert "# Lesson Commands" in help_text
        assert "/lesson list" in help_text
        assert "/lesson search" in help_text
        assert "/lesson show" in help_text
        assert "/lesson refresh" in help_text
        assert "Examples:" in help_text


class TestLessonList:
    """Tests for _lesson_list function."""

    def test_lesson_list_all(self, mock_lesson_index):
        """Test listing all lessons."""
        result = _lesson_list()

        assert "# Available Lessons" in result
        assert "## Tools" in result
        assert "## Patterns" in result
        assert "Patch Placeholders" in result
        assert "Browser Interaction" in result
        assert "Persistent Learning" in result
        assert "Total: 3 lessons" in result

    def test_lesson_list_by_category(self, mock_lesson_index):
        """Test listing lessons by category."""
        result = _lesson_list("tools")

        assert "# Available Lessons" in result
        assert "## Tools" in result
        assert "Patch Placeholders" in result
        assert "Browser Interaction" in result
        assert "Persistent Learning" not in result

    def test_lesson_list_empty_category(self, mock_lesson_index):
        """Test listing lessons for non-existent category."""
        result = _lesson_list("nonexistent")

        assert "No lessons found in category: nonexistent" in result

    def test_lesson_list_no_lessons(self):
        """Test listing when no lessons exist."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index = MagicMock()
            mock_index.lessons = []
            mock_index_class.return_value = mock_index

            result = _lesson_list()
            assert "No lessons found" in result

    def test_lesson_list_with_keywords(self, mock_lesson_index):
        """Test that keywords are displayed in listing."""
        result = _lesson_list()

        assert "patch" in result.lower()
        assert "placeholder" in result.lower()

    def test_lesson_list_error_handling(self):
        """Test error handling in lesson listing."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index_class.side_effect = Exception("Test error")

            result = _lesson_list()
            assert "Error listing lessons" in result
            assert "Test error" in result


class TestLessonSearch:
    """Tests for _lesson_search function."""

    def test_lesson_search_by_title(self, mock_lesson_index):
        """Test searching lessons by title."""
        result = _lesson_search("Patch")

        assert "# Lessons matching 'Patch'" in result
        assert "Patch Placeholders" in result
        assert "Browser Interaction" not in result

    def test_lesson_search_by_keyword(self, mock_lesson_index):
        """Test searching lessons by keyword."""
        result = _lesson_search("browser")

        assert "Browser Interaction" in result
        assert "Patch Placeholders" not in result

    def test_lesson_search_no_results(self, mock_lesson_index):
        """Test searching with no results."""
        result = _lesson_search("nonexistent")

        assert "No lessons found matching 'nonexistent'" in result

    def test_lesson_search_multiple_results(self, mock_lesson_index):
        """Test searching with multiple results."""
        result = _lesson_search("interaction")

        # Should match lessons with "interaction" in keywords or title
        assert "Browser Interaction" in result

    def test_lesson_search_shows_category_and_keywords(self, mock_lesson_index):
        """Test that search results show category and keywords."""
        result = _lesson_search("Patch")

        assert "**Category**:" in result
        assert "**Keywords**:" in result

    def test_lesson_search_limits_to_10_results(self):
        """Test that search limits results to 10."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index = MagicMock()

            # Create 15 lessons
            lessons = [
                create_test_lesson(title=f"Lesson {i}", keywords=["search"])
                for i in range(15)
            ]
            mock_index.lessons = lessons
            mock_index.search = lambda query: lessons  # Return all

            mock_index_class.return_value = mock_index

            result = _lesson_search("search")
            assert "... and 5 more" in result

    def test_lesson_search_error_handling(self):
        """Test error handling in lesson search."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index_class.side_effect = Exception("Test error")

            result = _lesson_search("test")
            assert "Error searching lessons" in result
            assert "Test error" in result


class TestLessonShow:
    """Tests for _lesson_show function."""

    def test_lesson_show_by_exact_title(self, mock_lesson_index):
        """Test showing lesson by exact title."""
        result = _lesson_show("Patch Placeholders")

        assert "# Patch Placeholders" in result
        assert "Rule: Never use undefined placeholders." in result

    def test_lesson_show_by_partial_title(self, mock_lesson_index):
        """Test showing lesson by partial title match."""
        result = _lesson_show("patch")

        assert "# Patch Placeholders" in result

    def test_lesson_show_by_filename(self, mock_lesson_index):
        """Test showing lesson by filename."""
        result = _lesson_show("patch-placeholders")

        assert "# Patch Placeholders" in result

    def test_lesson_show_not_found(self, mock_lesson_index):
        """Test showing non-existent lesson."""
        result = _lesson_show("nonexistent")

        assert "Lesson not found: nonexistent" in result

    def test_lesson_show_case_insensitive(self, mock_lesson_index):
        """Test that lesson show is case insensitive."""
        result = _lesson_show("PATCH PLACEHOLDERS")

        assert "# Patch Placeholders" in result

    def test_lesson_show_error_handling(self):
        """Test error handling in lesson show."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index_class.side_effect = Exception("Test error")

            result = _lesson_show("test")
            assert "Error showing lesson" in result
            assert "Test error" in result


class TestLessonRefresh:
    """Tests for _lesson_refresh function."""

    def test_lesson_refresh_success(self, mock_lesson_index):
        """Test successful lesson refresh."""
        result = _lesson_refresh()

        assert "✓ Refreshed lesson index" in result
        assert "3 lessons" in result
        mock_lesson_index.refresh.assert_called_once()

    def test_lesson_refresh_error_handling(self):
        """Test error handling in lesson refresh."""
        with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
            mock_index_class.side_effect = Exception("Test error")

            result = _lesson_refresh()
            assert "Error refreshing lessons" in result
            assert "Test error" in result


class TestLessonCommand:
    """Tests for main lesson command."""

    def test_lesson_command_no_args(self):
        """Test lesson command with no arguments shows help."""
        ctx = MagicMock()
        ctx.full_args = ""

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "# Lesson Commands" in messages[0].content

    def test_lesson_command_list(self, mock_lesson_index):
        """Test lesson command with list subcommand."""
        ctx = MagicMock()
        ctx.full_args = "list"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert messages[0].role == "system"
        assert "# Available Lessons" in messages[0].content

    def test_lesson_command_list_with_category(self, mock_lesson_index):
        """Test lesson command with list and category."""
        ctx = MagicMock()
        ctx.full_args = "list tools"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "## Tools" in messages[0].content

    def test_lesson_command_search(self, mock_lesson_index):
        """Test lesson command with search subcommand."""
        ctx = MagicMock()
        ctx.full_args = "search patch"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "Lessons matching 'patch'" in messages[0].content

    def test_lesson_command_search_no_query(self):
        """Test lesson command with search but no query."""
        ctx = MagicMock()
        ctx.full_args = "search"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "Usage: /lesson search" in messages[0].content

    def test_lesson_command_show(self, mock_lesson_index):
        """Test lesson command with show subcommand."""
        ctx = MagicMock()
        ctx.full_args = "show Patch Placeholders"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "# Patch Placeholders" in messages[0].content

    def test_lesson_command_show_no_name(self):
        """Test lesson command with show but no lesson name."""
        ctx = MagicMock()
        ctx.full_args = "show"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "Usage: /lesson show" in messages[0].content

    def test_lesson_command_refresh(self, mock_lesson_index):
        """Test lesson command with refresh subcommand."""
        ctx = MagicMock()
        ctx.full_args = "refresh"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "✓ Refreshed lesson index" in messages[0].content

    def test_lesson_command_unknown_subcommand(self):
        """Test lesson command with unknown subcommand."""
        ctx = MagicMock()
        ctx.full_args = "unknown"

        messages = list(lesson(ctx))

        assert len(messages) == 1
        assert "Unknown subcommand: unknown" in messages[0].content
        assert "# Lesson Commands" in messages[0].content
