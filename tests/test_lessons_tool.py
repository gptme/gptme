"""Tests for lessons tool integration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from gptme.lessons.matcher import MatchResult
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message
from gptme.tools.lessons import auto_include_lessons_hook, tool


@pytest.fixture
def sample_lesson():
    """Create a sample lesson for testing."""
    metadata = LessonMetadata(keywords=["patch", "file", "editing"])
    return Lesson(
        title="Patch Best Practices",
        category="tools",
        description="Best practices for using patch tool",
        body="# Patch Best Practices\n\nAlways verify patches before applying.",
        metadata=metadata,
        path=Path("/lessons/tools/patch.md"),
    )


@pytest.fixture
def sample_match(sample_lesson):
    """Create a sample lesson match for testing."""
    return MatchResult(
        lesson=sample_lesson,
        score=0.8,
        matched_by=["keyword: patch", "keyword: file"],
    )


@pytest.fixture
def mock_config():
    """Mock configuration."""
    with patch("gptme.tools.lessons.get_config") as mock:
        config = MagicMock()
        config.get_env_bool = MagicMock(return_value=True)
        config.get_env = MagicMock(return_value="5")
        mock.return_value = config
        yield config


@pytest.fixture
def user_message():
    """Create a sample user message."""
    return Message(role="user", content="How do I use the patch tool?")


@pytest.fixture
def conversation_log(user_message):
    """Create a sample conversation log."""
    return [
        Message(role="system", content="You are a helpful assistant."),
        user_message,
    ]


class TestAutoIncludeLessonsHook:
    """Tests for auto_include_lessons_hook."""

    def test_hook_disabled_by_config(self, conversation_log, mock_config):
        """Test that hook respects GPTME_LESSONS_AUTO_INCLUDE config."""
        mock_config.get_env_bool.return_value = False

        messages = list(auto_include_lessons_hook(conversation_log) or [])
        assert len(messages) == 0

        mock_config.get_env_bool.assert_called_once_with(
            "GPTME_LESSONS_AUTO_INCLUDE", True
        )

    def test_hook_no_user_message(self, mock_config):
        """Test hook with no user messages."""
        log = [Message(role="system", content="System message")]

        messages = list(auto_include_lessons_hook(log) or [])
        assert len(messages) == 0

    def test_hook_no_lessons_in_index(self, conversation_log, mock_config):
        """Test hook when no lessons exist."""
        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            mock_index = MagicMock()
            mock_index.lessons = []
            mock_get_index.return_value = mock_index

            messages = list(auto_include_lessons_hook(conversation_log) or [])
            assert len(messages) == 0

    def test_hook_no_matching_lessons(
        self, conversation_log, mock_config, sample_lesson
    ):
        """Test hook when no lessons match."""
        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.LessonMatcher") as mock_matcher_class:
                mock_index = MagicMock()
                mock_index.lessons = [sample_lesson]
                mock_get_index.return_value = mock_index

                mock_matcher = MagicMock()
                mock_matcher.match.return_value = []
                mock_matcher_class.return_value = mock_matcher

                messages = list(auto_include_lessons_hook(conversation_log) or [])
                assert len(messages) == 0

    def test_hook_includes_matching_lessons(
        self, conversation_log, mock_config, sample_lesson, sample_match
    ):
        """Test hook includes matching lessons."""
        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.LessonMatcher") as mock_matcher_class:
                mock_index = MagicMock()
                mock_index.lessons = [sample_lesson]
                mock_get_index.return_value = mock_index

                mock_matcher = MagicMock()
                mock_matcher.match.return_value = [sample_match]
                mock_matcher_class.return_value = mock_matcher

                messages = list(auto_include_lessons_hook(conversation_log) or [])

                assert len(messages) == 1
                message = messages[0]
                assert message.role == "system"
                assert "# Relevant Lessons" in message.content
                assert "Patch Best Practices" in message.content
                assert message.hide is True

    def test_hook_limits_max_lessons(self, conversation_log, mock_config):
        """Test hook respects max lessons limit."""
        # Create 10 lessons
        lessons = []
        matches = []
        for i in range(10):
            metadata = LessonMetadata(keywords=[f"keyword{i}"])
            lesson = Lesson(
                title=f"Lesson {i}",
                category="tools",
                description=f"Description {i}",
                body=f"Body {i}",
                metadata=metadata,
                path=Path(f"/lessons/lesson{i}.md"),
            )
            lessons.append(lesson)
            matches.append(
                MatchResult(
                    lesson=lesson,
                    score=0.8,
                    matched_by=[f"keyword{i}"],
                )
            )

        mock_config.get_env.return_value = "3"  # Limit to 3

        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.LessonMatcher") as mock_matcher_class:
                mock_index = MagicMock()
                mock_index.lessons = lessons
                mock_get_index.return_value = mock_index

                mock_matcher = MagicMock()
                mock_matcher.match.return_value = matches
                mock_matcher_class.return_value = mock_matcher

                messages = list(auto_include_lessons_hook(conversation_log) or [])

                assert len(messages) == 1
                content = messages[0].content

                # Should only include first 3 lessons
                assert "Lesson 0" in content
                assert "Lesson 1" in content
                assert "Lesson 2" in content
                assert "Lesson 9" not in content

    def test_hook_handles_invalid_max_lessons(self, conversation_log, mock_config):
        """Test hook with invalid max lessons config."""
        mock_config.get_env.return_value = "invalid"

        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            with patch("gptme.tools.lessons.LessonMatcher") as mock_matcher_class:
                mock_index = MagicMock()
                mock_index.lessons = []
                mock_get_index.return_value = mock_index

                mock_matcher = MagicMock()
                mock_matcher.match.return_value = []
                mock_matcher_class.return_value = mock_matcher

                # Should not raise error, should use default of 5
                messages = list(auto_include_lessons_hook(conversation_log) or [])
                assert len(messages) == 0

    def test_hook_handles_exception(self, conversation_log, mock_config):
        """Test hook handles exceptions gracefully."""
        with patch("gptme.tools.lessons._get_lesson_index") as mock_get_index:
            mock_get_index.side_effect = Exception("Test error")

            # Should not raise, should log warning
            messages = list(auto_include_lessons_hook(conversation_log) or [])
            assert len(messages) == 0


class TestToolSpec:
    """Tests for tool specification."""

    def test_tool_has_correct_name(self):
        """Test tool has correct name."""
        assert tool.name == "lessons"

    def test_tool_has_description(self):
        """Test tool has description."""
        assert tool.desc
        assert "lesson" in tool.desc.lower()

    def test_tool_has_instructions(self):
        """Test tool has instructions."""
        assert tool.instructions
        assert "lessons" in tool.instructions.lower()
        assert "keywords" in tool.instructions

    def test_tool_has_hooks(self):
        """Test tool has hooks configured."""
        assert "auto_include_lessons" in tool.hooks
        hook_info = tool.hooks["auto_include_lessons"]
        assert len(hook_info) == 3  # (hook_type, function, priority)

    def test_tool_has_commands(self):
        """Test tool has commands configured."""
        assert "lesson" in tool.commands
        assert callable(tool.commands["lesson"])
