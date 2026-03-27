"""Tests for the lessons tool (gptme/tools/lessons.py).

Tests cover:
- Lesson path extraction from conversation log
- Tool extraction from assistant messages
- Message content extraction
- Session statistics management
- Auto-include lessons hook
- Session end hook
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.codeblock import Codeblock
from gptme.lessons.matcher import MatchResult
from gptme.lessons.parser import Lesson, LessonMetadata
from gptme.message import Message
from gptme.tools.lessons import (
    LessonSessionStats,
    _extract_message_content,
    _extract_recent_tools,
    _get_included_lessons_from_log,
    _get_session_stats,
    _reset_session_stats,
    _session_stats_var,
    auto_include_lessons_hook,
    session_end_lessons_hook,
)

# --- Fixtures ---


def _msg(role: str, content: str) -> Message:
    """Helper to create a Message with minimal fields."""
    return Message(role=role, content=content)  # type: ignore


def _lesson(
    path: str = "/lessons/test.md",
    title: str = "Test Lesson",
    keywords: list[str] | None = None,
) -> Lesson:
    """Helper to create a Lesson."""
    return Lesson(
        path=Path(path),
        metadata=LessonMetadata(keywords=keywords or []),
        title=title,
        description="test",
        category="test",
        body="Test body content",
    )


def _match_result(lesson: Lesson | None = None, matched_by: list[str] | None = None):
    """Helper to create a MatchResult."""
    return MatchResult(
        lesson=lesson or _lesson(),
        score=1.0,
        matched_by=matched_by or ["keyword:test"],
    )


@pytest.fixture(autouse=True)
def _reset_stats():
    """Reset session stats between tests."""
    _session_stats_var.set(None)
    yield
    _session_stats_var.set(None)


# --- Tests for _get_included_lessons_from_log ---


class TestGetIncludedLessonsFromLog:
    def test_empty_log(self):
        assert _get_included_lessons_from_log([]) == set()

    def test_no_lesson_messages(self):
        log = [
            _msg("user", "hello"),
            _msg("assistant", "hi there"),
        ]
        assert _get_included_lessons_from_log(log) == set()

    def test_extracts_single_lesson(self):
        log = [
            _msg(
                "system",
                "# Relevant Lessons\n\n"
                "## My Lesson\n\n"
                "*Path: /lessons/tools/my-lesson.md*\n\n"
                "Content here",
            ),
        ]
        result = _get_included_lessons_from_log(log)
        assert result == {"/lessons/tools/my-lesson.md"}

    def test_extracts_multiple_lessons(self):
        log = [
            _msg(
                "system",
                "# Relevant Lessons\n\n"
                "## Lesson A\n\n"
                "*Path: /lessons/a.md*\n\n"
                "Body A\n\n"
                "## Lesson B\n\n"
                "*Path: /lessons/b.md*\n\n"
                "Body B",
            ),
        ]
        result = _get_included_lessons_from_log(log)
        assert result == {"/lessons/a.md", "/lessons/b.md"}

    def test_multiple_system_messages(self):
        log = [
            _msg(
                "system",
                "# Relevant Lessons\n\n*Path: /lessons/first.md*\nBody",
            ),
            _msg("user", "do something"),
            _msg(
                "system",
                "# Relevant Lessons\n\n*Path: /lessons/second.md*\nBody",
            ),
        ]
        result = _get_included_lessons_from_log(log)
        assert result == {"/lessons/first.md", "/lessons/second.md"}

    def test_ignores_non_lesson_system_messages(self):
        log = [
            _msg("system", "You are a helpful assistant"),
            _msg("system", "# Relevant Lessons\n\n*Path: /lessons/real.md*\nBody"),
            _msg("system", "Some other system message with *Path: fake*"),
        ]
        result = _get_included_lessons_from_log(log)
        assert result == {"/lessons/real.md"}

    def test_ignores_malformed_path_lines(self):
        log = [
            _msg(
                "system",
                "# Relevant Lessons\n\n"
                "*Path: /lessons/good.md*\n"
                "*Paths: /not/a/real/path*\n"
                "Path: /no/asterisks.md\n"
                "*Path: missing-end-asterisk\n",
            ),
        ]
        result = _get_included_lessons_from_log(log)
        assert result == {"/lessons/good.md"}


# --- Tests for _extract_recent_tools ---


class TestExtractRecentTools:
    def test_empty_log(self):
        assert _extract_recent_tools([]) == []

    def test_no_assistant_messages(self):
        log = [_msg("user", "hello"), _msg("system", "system msg")]
        assert _extract_recent_tools(log) == []

    def test_extracts_tool_from_codeblock(self):
        msg = _msg("assistant", "Let me patch the file\n```patch file.py\n+line\n```")
        result = _extract_recent_tools([msg])
        assert "patch" in result

    def test_ignores_text_and_markdown_blocks(self):
        msg = _msg(
            "assistant",
            "Here's some code:\n```text\nplain text\n```\n"
            "And markdown:\n```markdown\n# Title\n```",
        )
        result = _extract_recent_tools([msg])
        assert result == []

    def test_deduplicates_tools(self):
        # Use MagicMock to allow codeblock override
        msg = MagicMock(spec=Message)
        msg.role = "assistant"
        msg.get_codeblocks.return_value = [
            Codeblock(lang="shell", content="ls"),
            Codeblock(lang="shell", content="pwd"),
        ]
        result = _extract_recent_tools([msg])
        assert result.count("shell") == 1

    def test_preserves_order(self):
        msg = MagicMock(spec=Message)
        msg.role = "assistant"
        msg.get_codeblocks.return_value = [
            Codeblock(lang="python", content="print('hi')"),
            Codeblock(lang="shell", content="ls"),
            Codeblock(lang="python", content="x=1"),
        ]
        result = _extract_recent_tools([msg])
        assert result == ["python", "shell"]

    def test_respects_limit(self):
        old_msgs = []
        for i in range(20):
            m = MagicMock(spec=Message)
            m.role = "assistant"
            m.get_codeblocks.return_value = [Codeblock(lang=f"tool{i}", content="c")]
            old_msgs.append(m)
        recent = MagicMock(spec=Message)
        recent.role = "assistant"
        recent.get_codeblocks.return_value = [
            Codeblock(lang="recent_tool", content="c")
        ]
        log: list = old_msgs + [recent]
        result = _extract_recent_tools(log, limit=1)  # type: ignore[arg-type]
        assert "recent_tool" in result

    def test_extracts_tool_name_only(self):
        """Tool name should be first word only (e.g., 'save /path/to/file.py' -> 'save')."""
        msg = MagicMock(spec=Message)
        msg.role = "assistant"
        msg.get_codeblocks.return_value = [
            Codeblock(lang="save /path/to/file.py", content="print('hello')"),
        ]
        result = _extract_recent_tools([msg])
        assert result == ["save"]


# --- Tests for _extract_message_content ---


class TestExtractMessageContent:
    def test_empty_log(self):
        assert _extract_message_content([]) == ""

    def test_extracts_user_messages(self):
        log = [_msg("user", "how do I fix this bug")]
        result = _extract_message_content(log)
        assert "fix this bug" in result

    def test_extracts_assistant_messages(self):
        log = [_msg("assistant", "let me help with that")]
        result = _extract_message_content(log)
        assert "let me help" in result

    def test_ignores_system_messages(self):
        log = [
            _msg("system", "secret system content"),
            _msg("user", "visible content"),
        ]
        result = _extract_message_content(log)
        assert "secret" not in result
        assert "visible" in result

    def test_respects_limit(self):
        old = [_msg("user", f"old message {i}") for i in range(20)]
        recent = [_msg("user", "recent message")]
        result = _extract_message_content(old + recent, limit=2)
        assert "recent message" in result

    def test_chronological_order(self):
        log = [
            _msg("user", "first"),
            _msg("assistant", "second"),
        ]
        result = _extract_message_content(log)
        first_pos = result.find("first")
        second_pos = result.find("second")
        assert first_pos < second_pos


# --- Tests for session stats ---


class TestSessionStats:
    def test_get_creates_new(self):
        stats = _get_session_stats()
        assert isinstance(stats, LessonSessionStats)
        assert stats.total_matched == 0
        assert len(stats.unique_lessons) == 0

    def test_get_returns_same_instance(self):
        stats1 = _get_session_stats()
        stats2 = _get_session_stats()
        assert stats1 is stats2

    def test_reset_clears_stats(self):
        stats = _get_session_stats()
        stats.total_matched = 5
        stats.unique_lessons.add("/some/lesson.md")

        _reset_session_stats()

        fresh = _get_session_stats()
        assert fresh.total_matched == 0
        assert len(fresh.unique_lessons) == 0

    def test_stats_track_lessons(self):
        stats = _get_session_stats()
        stats.unique_lessons.add("/lessons/a.md")
        stats.unique_lessons.add("/lessons/b.md")
        stats.lesson_titles["/lessons/a.md"] = "Lesson A"
        stats.total_matched = 2

        assert len(stats.unique_lessons) == 2
        assert stats.lesson_titles["/lessons/a.md"] == "Lesson A"


# --- Tests for auto_include_lessons_hook ---


class TestAutoIncludeLessonsHook:
    def _make_manager(
        self, messages: list[Message] | None = None, chat_id: str = "test-chat"
    ):
        """Create a mock LogManager."""
        manager = MagicMock()
        manager.log.messages = messages or []
        manager.chat_id = chat_id
        return manager

    @patch("gptme.tools.lessons.get_config")
    def test_disabled_by_config(self, mock_config):
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: (
            False if key == "GPTME_LESSONS_AUTO_INCLUDE" else default
        )
        mock_config.return_value = config

        manager = self._make_manager([_msg("user", "test")])
        results = list(auto_include_lessons_hook(manager))
        assert results == []

    @patch("gptme.tools.lessons.get_config")
    def test_no_content_skips(self, mock_config):
        """When there are no user/assistant messages, hook returns nothing."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        # Only system messages - no user/assistant content
        manager = self._make_manager([_msg("system", "system prompt")])
        results = list(auto_include_lessons_hook(manager))
        assert results == []

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_includes_matching_lessons(self, mock_config, mock_index):
        """When lessons match, they're included as a system message."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        lesson = _lesson(
            path="/lessons/test/example.md",
            title="Example Lesson",
            keywords=["fix bug"],
        )
        mock_idx = MagicMock()
        mock_idx.lessons = [lesson]
        mock_index.return_value = mock_idx

        manager = self._make_manager([_msg("user", "I need to fix a bug in the code")])

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            mock_matcher = MagicMock()
            mock_matcher.match.return_value = [_match_result(lesson)]
            MockMatcher.return_value = mock_matcher

            results = list(auto_include_lessons_hook(manager))

        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "system"
        assert "Relevant Lessons" in msg.content
        assert "Example Lesson" in msg.content
        assert msg.hide is True

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_skips_already_included(self, mock_config, mock_index):
        """Lessons already in the log are not re-included."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        lesson = _lesson(path="/lessons/already.md", title="Already Included")
        mock_idx = MagicMock()
        mock_idx.lessons = [lesson]
        mock_index.return_value = mock_idx

        # Already included in conversation
        manager = self._make_manager(
            [
                _msg(
                    "system",
                    "# Relevant Lessons\n\n*Path: /lessons/already.md*\nBody",
                ),
                _msg("user", "test query"),
            ]
        )

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            mock_matcher = MagicMock()
            mock_matcher.match.return_value = [_match_result(lesson)]
            MockMatcher.return_value = mock_matcher

            results = list(auto_include_lessons_hook(manager))

        assert results == []

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_respects_session_limit(self, mock_config, mock_index):
        """Session lesson limit prevents including too many."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "2"  # Max 2 lessons per session
        mock_config.return_value = config

        # Pre-populate stats with 2 lessons already included
        stats = _get_session_stats()
        stats.unique_lessons = {"/a.md", "/b.md"}
        stats.total_matched = 2

        manager = self._make_manager([_msg("user", "test")])

        results = list(auto_include_lessons_hook(manager))
        assert results == []

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_updates_session_stats(self, mock_config, mock_index):
        """Session stats are updated after including lessons."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        lesson = _lesson(path="/lessons/new.md", title="New Lesson")
        mock_idx = MagicMock()
        mock_idx.lessons = [lesson]
        mock_index.return_value = mock_idx

        manager = self._make_manager([_msg("user", "trigger content")])

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            mock_matcher = MagicMock()
            mock_matcher.match.return_value = [_match_result(lesson)]
            MockMatcher.return_value = mock_matcher

            list(auto_include_lessons_hook(manager))

        stats = _get_session_stats()
        assert "/lessons/new.md" in stats.unique_lessons
        assert stats.total_matched == 1
        assert stats.lesson_titles["/lessons/new.md"] == "New Lesson"

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_limits_to_remaining_budget(self, mock_config, mock_index):
        """When near session limit, only includes remaining budget worth."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "3"  # Max 3 lessons
        mock_config.return_value = config

        # Already have 2, so only 1 more allowed
        stats = _get_session_stats()
        stats.unique_lessons = {"/existing1.md", "/existing2.md"}
        stats.total_matched = 2

        lessons = [
            _lesson(path=f"/lessons/new{i}.md", title=f"New {i}") for i in range(3)
        ]
        mock_idx = MagicMock()
        mock_idx.lessons = lessons
        mock_index.return_value = mock_idx

        manager = self._make_manager([_msg("user", "trigger")])

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            mock_matcher = MagicMock()
            mock_matcher.match.return_value = [_match_result(les) for les in lessons]
            MockMatcher.return_value = mock_matcher

            results = list(auto_include_lessons_hook(manager))

        # Should include only 1 (budget = 3 - 2 = 1)
        assert len(results) == 1
        stats = _get_session_stats()
        assert len(stats.unique_lessons) == 3  # 2 existing + 1 new

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_initializes_stats_from_log(self, mock_config, mock_index):
        """Stats are initialized from existing log on first call."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        mock_idx = MagicMock()
        mock_idx.lessons = []
        mock_index.return_value = mock_idx

        # Log already has a lesson from previous turn
        manager = self._make_manager(
            [
                _msg(
                    "system",
                    "# Relevant Lessons\n\n*Path: /old.md*\nBody",
                ),
                _msg("user", "new question"),
            ]
        )

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            MockMatcher.return_value.match.return_value = []
            list(auto_include_lessons_hook(manager))

        stats = _get_session_stats()
        assert "/old.md" in stats.unique_lessons

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_handles_exception_gracefully(self, mock_config, mock_index):
        """Errors in lesson matching don't crash the hook."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        mock_index.side_effect = RuntimeError("broken index")

        manager = self._make_manager([_msg("user", "trigger")])
        # Should not raise
        results = list(auto_include_lessons_hook(manager))
        assert results == []

    @patch("gptme.tools.lessons._get_lesson_index")
    @patch("gptme.tools.lessons.get_config")
    def test_lesson_message_format(self, mock_config, mock_index):
        """Verify the format of included lesson messages."""
        config = MagicMock()
        config.get_env_bool.side_effect = lambda key, default: default
        config.get_env.return_value = "20"
        mock_config.return_value = config

        lesson = _lesson(
            path="/lessons/tools/shell.md",
            title="Shell Best Practices",
        )
        lesson = Lesson(
            path=Path("/lessons/tools/shell.md"),
            metadata=LessonMetadata(keywords=["shell"]),
            title="Shell Best Practices",
            description="How to use shell safely",
            category="tools",
            body="Always quote variables.",
        )
        mock_idx = MagicMock()
        mock_idx.lessons = [lesson]
        mock_index.return_value = mock_idx

        manager = self._make_manager([_msg("user", "shell command")])

        with patch("gptme.tools.lessons.LessonMatcher") as MockMatcher:
            mock_matcher = MagicMock()
            mock_matcher.match.return_value = [
                MatchResult(
                    lesson=lesson,
                    score=1.0,
                    matched_by=["keyword:shell"],
                )
            ]
            MockMatcher.return_value = mock_matcher

            results = list(auto_include_lessons_hook(manager))

        assert len(results) == 1
        assert isinstance(results[0], Message)
        content = results[0].content
        assert "# Relevant Lessons" in content
        assert "## Shell Best Practices" in content
        assert "*Path: /lessons/tools/shell.md*" in content
        assert "*Category: tools*" in content
        assert "*Matched by: 1 keyword(s)*" in content
        assert "Always quote variables." in content


# --- Tests for session_end_lessons_hook ---


class TestSessionEndLessonsHook:
    def _make_manager(self):
        manager = MagicMock()
        manager.log.messages = []
        return manager

    def test_no_stats_yields_nothing(self):
        manager = self._make_manager()
        results = list(session_end_lessons_hook(manager))
        assert results == []

    def test_empty_stats_yields_nothing(self):
        _get_session_stats()  # Initialize but don't add anything
        manager = self._make_manager()
        results = list(session_end_lessons_hook(manager))
        assert results == []

    @patch("gptme.util.console")
    def test_prints_summary_with_stats(self, mock_console):
        stats = _get_session_stats()
        stats.total_matched = 5
        stats.unique_lessons = {"/a.md", "/b.md", "/c.md"}

        manager = self._make_manager()
        list(session_end_lessons_hook(manager))

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "3 unique lessons" in call_args
        assert "5 total matches" in call_args

    @patch("gptme.util.console")
    def test_resets_stats_after_summary(self, mock_console):
        stats = _get_session_stats()
        stats.total_matched = 3
        stats.unique_lessons = {"/a.md"}

        manager = self._make_manager()
        list(session_end_lessons_hook(manager))

        # Stats should be reset
        new_stats = _get_session_stats()
        assert new_stats.total_matched == 0
        assert len(new_stats.unique_lessons) == 0


# --- Tests for tool specification ---


class TestToolSpec:
    def test_tool_spec_exists(self):
        from gptme.tools.lessons import tool

        assert tool.name == "lessons"
        assert "lesson" in tool.commands

    def test_hooks_registered(self):
        from gptme.tools.lessons import tool

        assert "auto_include_lessons" in tool.hooks
        assert "session_end_lessons" in tool.hooks

    def test_hook_types(self):
        from gptme.tools.lessons import tool

        auto_hook = tool.hooks["auto_include_lessons"]
        end_hook = tool.hooks["session_end_lessons"]
        # Hooks are tuples of (hook_type, function, priority)
        assert auto_hook[0] == "step.pre"
        assert end_hook[0] == "session.end"
