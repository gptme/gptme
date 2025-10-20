"""Integration tests using actual lesson files from docs/lessons/."""

from pathlib import Path
from unittest.mock import patch

import pytest
from gptme.lessons import LessonIndex, LessonMatcher, MatchContext
from gptme.message import Message
from gptme.tools.lessons import _extract_recent_tools, auto_include_lessons_hook

# Path to example lessons
DOCS_LESSONS_DIR = Path(__file__).parent.parent / "docs" / "lessons"


@pytest.fixture
def docs_lesson_index():
    """Create a LessonIndex using docs/lessons/ directory."""
    if not DOCS_LESSONS_DIR.exists():
        pytest.skip(f"Example lessons not found at {DOCS_LESSONS_DIR}")

    return LessonIndex(lesson_dirs=[DOCS_LESSONS_DIR])


class TestDocsLessonsParsing:
    """Tests that docs/lessons/ files parse correctly."""

    def test_docs_lessons_exist(self):
        """Test that docs/lessons directory exists."""
        assert (
            DOCS_LESSONS_DIR.exists()
        ), f"docs/lessons not found at {DOCS_LESSONS_DIR}"

        # Check subdirectories exist
        tools_dir = DOCS_LESSONS_DIR / "tools"
        workflows_dir = DOCS_LESSONS_DIR / "workflows"

        assert tools_dir.exists(), "docs/lessons/tools directory missing"
        assert workflows_dir.exists(), "docs/lessons/workflows directory missing"

    def test_docs_lessons_can_be_indexed(self, docs_lesson_index):
        """Test that docs lessons can be indexed."""
        assert len(docs_lesson_index.lessons) > 0, "No lessons found in docs/lessons"

        # We created 5 lessons
        assert len(docs_lesson_index.lessons) >= 5

    def test_patch_lesson_parsed_correctly(self, docs_lesson_index):
        """Test that patch.md is parsed correctly."""
        patch_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "patch" in lesson.title.lower()
        ]

        assert len(patch_lessons) > 0, "Patch lesson not found"

        lesson = patch_lessons[0]
        assert lesson.title == "Editing Files with Patch Tool"
        assert lesson.category == "tools"
        assert "patch" in lesson.metadata.keywords
        assert "edit" in lesson.metadata.keywords
        assert "patch" in lesson.metadata.tools or "morph" in lesson.metadata.tools

    def test_shell_lesson_parsed_correctly(self, docs_lesson_index):
        """Test that shell.md is parsed correctly."""
        shell_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "shell" in lesson.title.lower()
        ]

        assert len(shell_lessons) > 0, "Shell lesson not found"

        lesson = shell_lessons[0]
        assert "shell" in lesson.metadata.keywords
        assert "shell" in lesson.metadata.tools

    def test_python_lesson_parsed_correctly(self, docs_lesson_index):
        """Test that python.md is parsed correctly."""
        python_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "python" in lesson.title.lower()
        ]

        assert len(python_lessons) > 0, "Python lesson not found"

        lesson = python_lessons[0]
        assert "python" in lesson.metadata.keywords
        assert "ipython" in lesson.metadata.tools

    def test_browser_lesson_parsed_correctly(self, docs_lesson_index):
        """Test that browser.md is parsed correctly."""
        browser_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "browser" in lesson.title.lower() or "web" in lesson.title.lower()
        ]

        assert len(browser_lessons) > 0, "Browser lesson not found"

        lesson = browser_lessons[0]
        assert (
            "browser" in lesson.metadata.keywords or "web" in lesson.metadata.keywords
        )
        assert "browser" in lesson.metadata.tools

    def test_git_lesson_parsed_correctly(self, docs_lesson_index):
        """Test that git.md is parsed correctly."""
        git_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "git" in lesson.title.lower()
        ]

        assert len(git_lessons) > 0, "Git lesson not found"

        lesson = git_lessons[0]
        assert lesson.category == "workflows"
        assert "git" in lesson.metadata.keywords


class TestDocsLessonsMatching:
    """Tests that docs lessons match correctly."""

    def test_keyword_matching_patch(self, docs_lesson_index):
        """Test keyword matching for patch lesson."""
        matcher = LessonMatcher()
        context = MatchContext(message="How do I use the patch tool to edit files?")

        matches = matcher.match(docs_lesson_index.lessons, context)

        # Should match patch lesson
        patch_matches = [m for m in matches if "patch" in m.lesson.title.lower()]
        assert len(patch_matches) > 0, "Patch lesson should match"

        # Check matched_by contains keywords
        patch_match = patch_matches[0]
        assert any("keyword:" in mb for mb in patch_match.matched_by)

    def test_keyword_matching_shell(self, docs_lesson_index):
        """Test keyword matching for shell lesson."""
        matcher = LessonMatcher()
        context = MatchContext(message="How do I run shell commands?")

        matches = matcher.match(docs_lesson_index.lessons, context)

        # Should match shell lesson
        shell_matches = [m for m in matches if "shell" in m.lesson.title.lower()]
        assert len(shell_matches) > 0, "Shell lesson should match"

    def test_tool_matching_patch(self, docs_lesson_index):
        """Test tool-based matching for patch lesson."""
        matcher = LessonMatcher()
        context = MatchContext(message="Let me modify this file", tools_used=["patch"])

        matches = matcher.match(docs_lesson_index.lessons, context)

        # Should match patch lesson via tool
        patch_matches = [m for m in matches if "patch" in m.lesson.title.lower()]
        assert len(patch_matches) > 0, "Patch lesson should match via tool"

        # Check matched_by contains tool
        patch_match = patch_matches[0]
        assert any("tool:" in mb for mb in patch_match.matched_by)

    def test_tool_matching_ipython(self, docs_lesson_index):
        """Test tool-based matching for Python lesson."""
        matcher = LessonMatcher()
        context = MatchContext(
            message="Let me analyze this data", tools_used=["ipython"]
        )

        matches = matcher.match(docs_lesson_index.lessons, context)

        # Should match Python lesson via tool
        python_matches = [m for m in matches if "python" in m.lesson.title.lower()]
        assert len(python_matches) > 0, "Python lesson should match via tool"

    def test_combined_keyword_and_tool_matching(self, docs_lesson_index):
        """Test matching with both keywords and tools."""
        matcher = LessonMatcher()
        context = MatchContext(
            message="Use patch to edit the file", tools_used=["patch"]
        )

        matches = matcher.match(docs_lesson_index.lessons, context)

        # Should match patch lesson with high score
        patch_matches = [m for m in matches if "patch" in m.lesson.title.lower()]
        assert len(patch_matches) > 0

        patch_match = patch_matches[0]
        # Should have both keyword and tool matches
        has_keyword = any("keyword:" in mb for mb in patch_match.matched_by)
        has_tool = any("tool:" in mb for mb in patch_match.matched_by)
        assert has_keyword and has_tool, "Should match via both keywords and tools"


class TestDocsLessonsAutoInclude:
    """Tests auto-include functionality with docs lessons."""

    def test_extract_recent_tools_from_log(self):
        """Test extracting recent tool usage from conversation log."""
        log = [
            Message(role="user", content="Use patch to edit file"),
            Message(role="assistant", content="```patch example.py\ncode\n```"),
            Message(role="user", content="Now run it"),
            Message(role="assistant", content="```shell\npython example.py\n```"),
        ]

        tools = _extract_recent_tools(log)

        assert "patch" in tools
        assert "shell" in tools

    def test_auto_include_with_patch_keyword(self, docs_lesson_index):
        """Test auto-include with patch keyword."""
        log = [
            Message(role="user", content="How do I use the patch tool?"),
        ]

        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.get_config") as mock_config:
                # Setup mocks
                mock_get_index.return_value = docs_lesson_index
                mock_cfg = mock_config.return_value
                mock_cfg.get_env_bool.return_value = True
                mock_cfg.get_env.return_value = "5"

                messages = list(auto_include_lessons_hook(log) or [])

                assert len(messages) > 0, "Should include lessons"

                lesson_msg = messages[0]
                assert lesson_msg.role == "system"
                assert "# Relevant Lessons" in lesson_msg.content
                assert "Patch" in lesson_msg.content or "patch" in lesson_msg.content

    def test_auto_include_with_tool_usage(self, docs_lesson_index):
        """Test auto-include based on recent tool usage."""
        log = [
            Message(role="user", content="Edit this file"),
            Message(role="assistant", content="```patch example.py\ncode\n```"),
            Message(role="user", content="Looks good"),
        ]

        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.get_config") as mock_config:
                # Setup mocks
                mock_get_index.return_value = docs_lesson_index
                mock_cfg = mock_config.return_value
                mock_cfg.get_env_bool.return_value = True
                mock_cfg.get_env.return_value = "5"

                messages = list(auto_include_lessons_hook(log) or [])

                assert len(messages) > 0, "Should include lessons based on tool usage"

    def test_deduplication_from_history(self, docs_lesson_index):
        """Test that lessons aren't included twice."""
        patch_lesson = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "patch" in lesson.title.lower()
        ][0]

        log = [
            Message(role="user", content="Use patch"),
            Message(
                role="system",
                content=f"# Relevant Lessons\n\n*Path: {patch_lesson.path}*\n\nLesson content",
                hide=True,
            ),
            Message(role="user", content="Use patch again"),
        ]

        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.get_config") as mock_config:
                # Setup mocks
                mock_get_index.return_value = docs_lesson_index
                mock_cfg = mock_config.return_value
                mock_cfg.get_env_bool.return_value = True
                mock_cfg.get_env.return_value = "5"

                messages = list(auto_include_lessons_hook(log) or [])

                # Should not include patch lesson again since it's in history
                if messages:
                    assert (
                        str(patch_lesson.path) not in messages[0].content
                        or "# Relevant Lessons" not in messages[0].content
                    ), "Should not include already-included lessons"


class TestDocsLessonsREADME:
    """Tests related to docs/lessons/README.md."""

    def test_readme_exists(self):
        """Test that README.md exists."""
        readme = DOCS_LESSONS_DIR / "README.md"
        assert readme.exists(), "README.md should exist in docs/lessons"

    def test_readme_not_indexed_as_lesson(self, docs_lesson_index):
        """Test that README.md is not indexed as a lesson."""
        readme_lessons = [
            lesson
            for lesson in docs_lesson_index.lessons
            if "readme" in lesson.title.lower()
        ]
        assert len(readme_lessons) == 0, "README.md should not be indexed as a lesson"
