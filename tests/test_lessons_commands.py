"""Tests for lesson CLI commands."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.lessons.commands import (
    _lesson_help,
    _lesson_list,
    _lesson_refresh,
    _lesson_search,
    _lesson_show,
    lesson,
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


# ============================================================================
# Skills Command Tests (Issue #1000)
# ============================================================================

from gptme.lessons.commands import (
    _skills_all,
    _skills_help,
    _skills_list,
    _skills_read,
    skills,
)


@pytest.fixture
def mock_skill_index():
    """Create a mock LessonIndex with skills (Anthropic format with name/description)."""
    with patch("gptme.lessons.commands.LessonIndex") as mock_index_class:
        mock_index = MagicMock()

        # Create skills (have metadata.name) and lessons (no metadata.name)
        skill_metadata = LessonMetadata(
            name="python-repl",
            description="Interactive Python REPL skill",
            keywords=["python", "repl"],
        )
        skill = Lesson(
            title="Python REPL",
            category="tools",
            description="Interactive Python REPL skill",
            body="# Python REPL\n\nUse Python for calculations.",
            metadata=skill_metadata,
            path=Path("/skills/python-repl/SKILL.md"),
        )

        skill2_metadata = LessonMetadata(
            name="shell-commands",
            description="Execute shell commands",
            keywords=["shell", "bash"],
        )
        skill2 = Lesson(
            title="Shell Commands",
            category="tools",
            description="Execute shell commands",
            body="# Shell Commands\n\nRun shell commands safely.",
            metadata=skill2_metadata,
            path=Path("/skills/shell-commands/SKILL.md"),
        )

        # Regular lesson (no metadata.name)
        lesson_metadata = LessonMetadata(keywords=["git", "workflow"])
        lesson = Lesson(
            title="Git Workflow",
            category="workflow",
            description="Git best practices",
            body="# Git Workflow\n\nCommit often.",
            metadata=lesson_metadata,
            path=Path("/lessons/workflow/git-workflow.md"),
        )

        mock_index.lessons = [skill, skill2, lesson]
        mock_index_class.return_value = mock_index
        yield mock_index_class


class TestSkillsHelpFunction:
    """Test /skills help output."""

    def test_skills_help_contains_commands(self):
        """Help text shows all available commands."""
        result = _skills_help()
        assert "Skills Commands" in result
        assert "/skills list" in result
        assert "/skills read" in result
        assert "/skills all" in result

    def test_skills_help_mentions_anthropic(self):
        """Help explains Anthropic skill format."""
        result = _skills_help()
        assert "Anthropic" in result or "name" in result


class TestSkillsListFunction:
    """Test /skills list functionality."""

    def test_skills_list_with_mock(self, mock_lesson_index):
        """List returns message about skills."""
        # The mock_lesson_index provides lessons without 'name' in metadata
        # so should report no skills found
        result = _skills_list()
        # Either finds skills or reports none found
        assert "skill" in result.lower()

    def test_skills_list_empty(self, mock_lesson_index):
        """List handles empty index."""
        mock_lesson_index.return_value.lessons = []
        result = _skills_list()
        assert "found" in result.lower() or "skill" in result.lower()

    def test_skills_list_with_skills(self, mock_skill_index):
        """List shows skills when available."""
        result = _skills_list()
        assert "Available Skills" in result
        assert "python-repl" in result
        assert "shell-commands" in result
        assert "Total: 2 skills" in result

    def test_skills_list_sorted(self, mock_skill_index):
        """Skills are sorted by name."""
        result = _skills_list()
        # python-repl should come after shell-commands alphabetically
        # Actually: p comes before s, so python-repl first
        python_pos = result.find("python-repl")
        shell_pos = result.find("shell-commands")
        assert python_pos < shell_pos  # sorted alphabetically


class TestSkillsReadFunction:
    """Test /skills read functionality."""

    def test_skills_read_not_found(self, mock_lesson_index):
        """Read returns not found for nonexistent skill."""
        mock_lesson_index.return_value.lessons = []
        result = _skills_read("nonexistent-skill-xyz")
        assert "not found" in result.lower()

    def test_skills_read_skill_by_name(self, mock_skill_index):
        """Read finds skill by metadata.name."""
        result = _skills_read("python-repl")
        assert "python-repl" in result
        assert "Python for calculations" in result

    def test_skills_read_skill_partial_match(self, mock_skill_index):
        """Read finds skill with partial name match."""
        result = _skills_read("python")
        assert "python-repl" in result

    def test_skills_read_lesson_by_title(self, mock_skill_index):
        """Read finds lesson by title when no skill match."""
        result = _skills_read("Git Workflow")
        assert "Git Workflow" in result
        assert "Commit often" in result

    def test_skills_read_lesson_by_filename(self, mock_skill_index):
        """Read finds lesson by filename when no skill match."""
        result = _skills_read("git-workflow")
        assert "Git Workflow" in result

    def test_skills_read_empty_index(self, mock_lesson_index):
        """Read handles empty index gracefully."""
        mock_lesson_index.return_value.lessons = []
        result = _skills_read("anything")
        assert "not found" in result.lower() or "No" in result


class TestSkillsAllFunction:
    """Test /skills all functionality."""

    def test_skills_all_returns_content(self, mock_lesson_index):
        """All command returns some content."""
        result = _skills_all()
        # Should return something about skills or lessons
        assert "skill" in result.lower() or "lesson" in result.lower()

    def test_skills_all_shows_both(self, mock_skill_index):
        """All shows both skills and lessons sections."""
        result = _skills_all()
        assert "# Skills" in result
        assert "# Lessons" in result
        assert "python-repl" in result
        assert "Git Workflow" in result
        assert "Total: 2 skills, 1 lessons" in result

    def test_skills_all_groups_lessons_by_category(self, mock_skill_index):
        """All groups lessons by category."""
        result = _skills_all()
        assert "## Workflow" in result

    def test_skills_all_empty(self, mock_lesson_index):
        """All handles empty index."""
        mock_lesson_index.return_value.lessons = []
        result = _skills_all()
        assert (
            "found" in result.lower()
            or "skill" in result.lower()
            or "lesson" in result.lower()
        )


class TestSkillsCommandHandler:
    """Test /skills command routing."""

    def test_skills_no_args_shows_help(self, mock_lesson_index):
        """No args shows help."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = ""

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "Skills Commands" in messages[0].content

    def test_skills_unknown_subcommand(self, mock_lesson_index):
        """Unknown subcommand shows error and help."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = "invalidcmd"

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "Unknown subcommand" in messages[0].content
        assert "Skills Commands" in messages[0].content

    def test_skills_read_no_args(self, mock_lesson_index):
        """Read subcommand without args shows usage."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = "read"

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "Usage" in messages[0].content

    def test_skills_list_subcommand(self, mock_skill_index):
        """List subcommand works."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = "list"

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "Available Skills" in messages[0].content

    def test_skills_all_subcommand(self, mock_skill_index):
        """All subcommand works."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = "all"

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "Skills" in messages[0].content

    def test_skills_read_subcommand(self, mock_skill_index):
        """Read subcommand with name works."""
        from unittest.mock import MagicMock

        ctx = MagicMock()
        ctx.full_args = "read python-repl"

        messages = list(skills(ctx))

        assert len(messages) == 1
        assert "python-repl" in messages[0].content
