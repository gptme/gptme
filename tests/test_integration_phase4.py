"""Phase 4 integration tests for context selector with real conversations.

Tests the full workflow:
- Lesson selection with real Message objects
- File selection with real Message objects
- All three strategies (rule-based, LLM-based, hybrid)
- End-to-end validation
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from gptme.context_selector import (
    ContextSelectorConfig,
    FileSelectorConfig,
    select_relevant_files,
)
from gptme.lessons import (
    EnhancedLessonMatcher,
    Lesson,
    LessonMetadata,
    LessonSelectorConfig,
    MatchContext,
)
from gptme.message import Message


@pytest.fixture
def conversation_about_git():
    """Create a realistic conversation about git workflows."""
    return [
        Message(
            role="user",
            content="I need help with git workflow",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="I can help with git. What specific aspect?",
            timestamp=datetime.now(),
        ),
        Message(
            role="user",
            content="How do I create a branch and push it correctly?",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="Let me show you the git worktree workflow",
            timestamp=datetime.now(),
        ),
    ]


@pytest.fixture
def conversation_about_shell():
    """Create a realistic conversation about shell commands."""
    return [
        Message(
            role="user",
            content="I'm having issues with shell commands",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="What shell errors are you seeing?",
            timestamp=datetime.now(),
        ),
        Message(
            role="user",
            content="Getting 'cd: too many arguments' error",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="This is likely a path quoting issue",
            timestamp=datetime.now(),
        ),
    ]


@pytest.fixture
def conversation_mixed_topics():
    """Create a realistic conversation with multiple topics."""
    return [
        Message(
            role="user",
            content="I need to commit my changes and then run some tests",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="First let's handle the git commit",
            timestamp=datetime.now(),
        ),
        Message(
            role="user",
            content="Also, how do I quote paths with spaces in shell?",
            timestamp=datetime.now(),
        ),
        Message(
            role="assistant",
            content="Use quotes around the path",
            timestamp=datetime.now(),
        ),
    ]


def create_test_lesson(
    path: str = "test.md",
    keywords: list[str] | None = None,
    category: str = "general",
    title: str = "Test Lesson",
    content: str = "Test lesson content",
) -> Lesson:
    """Helper to create test lessons."""
    metadata = LessonMetadata(
        keywords=keywords or [],
        tools=[],
        status="active",
    )
    return Lesson(
        path=Path(path),
        metadata=metadata,
        title=title,
        description=f"{title} description",
        category=category,
        body=content,
    )


def messages_to_context(messages: list[Message]) -> MatchContext:
    """Convert a list of Messages to a MatchContext."""
    # Concatenate all message contents
    message_text = "\n".join(f"{msg.role}: {msg.content}" for msg in messages)
    return MatchContext(message=message_text)


@pytest.fixture
def sample_lessons():
    """Create sample lessons for testing."""
    return [
        create_test_lesson(
            path="git-workflow.md",
            keywords=["git", "workflow", "commit"],
            category="workflow",
            title="Git Workflow",
            content="Always commit with proper messages",
        ),
        create_test_lesson(
            path="shell-path-quoting.md",
            keywords=["shell", "path", "quoting"],
            category="tools",
            title="Shell Path Quoting",
            content="Quote paths with spaces",
        ),
        create_test_lesson(
            path="test-before-push.md",
            keywords=["test", "ci", "build"],
            category="workflow",
            title="Test Before Push",
            content="Run tests before pushing",
        ),
    ]


class TestLessonSelectionIntegration:
    """Test lesson selection with real conversations."""

    def test_rule_based_selection_git_conversation(
        self, conversation_about_git, sample_lessons
    ):
        """Test rule-based lesson selection for git conversation."""
        selector_config = ContextSelectorConfig(strategy="rule")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=False,
        )

        # Match lessons based on conversation
        context = messages_to_context(conversation_about_git)
        results = matcher.match(sample_lessons, context)

        # Should match git workflow lesson
        assert len(results) >= 1
        assert any("git" in result.lesson.title.lower() for result in results)

    def test_rule_based_selection_shell_conversation(
        self, conversation_about_shell, sample_lessons
    ):
        """Test rule-based lesson selection for shell conversation."""
        selector_config = ContextSelectorConfig(strategy="rule")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=False,
        )

        context = messages_to_context(conversation_about_shell)
        results = matcher.match(sample_lessons, context)

        # Should match shell lesson
        assert len(results) >= 1
        assert any("shell" in result.lesson.title.lower() for result in results)

    def test_rule_based_selection_mixed_conversation(
        self, conversation_mixed_topics, sample_lessons
    ):
        """Test rule-based lesson selection for mixed topic conversation."""
        selector_config = ContextSelectorConfig(strategy="rule")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=False,
        )

        context = messages_to_context(conversation_mixed_topics)
        results = matcher.match(sample_lessons, context)

        # Should match multiple lessons (git + shell)
        assert len(results) >= 2
        titles = [result.lesson.title.lower() for result in results]
        assert any("git" in title for title in titles)
        assert any("shell" in title for title in titles)

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_llm_based_selection_git_conversation(
        self, conversation_about_git, sample_lessons
    ):
        """Test LLM-based lesson selection for git conversation."""
        selector_config = ContextSelectorConfig(strategy="llm")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=True,
        )

        context = messages_to_context(conversation_about_git)
        results = await matcher.match_with_selector(
            sample_lessons, context, max_results=3
        )

        # LLM should select relevant lessons
        assert len(results) >= 1
        assert len(results) <= 3
        # Git lesson should be highly ranked
        assert any("git" in result.lesson.title.lower() for result in results[:2])

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_hybrid_selection_mixed_conversation(
        self, conversation_mixed_topics, sample_lessons
    ):
        """Test hybrid lesson selection for mixed conversation."""
        selector_config = ContextSelectorConfig(strategy="hybrid")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=True,
        )

        context = messages_to_context(conversation_mixed_topics)
        results = await matcher.match_with_selector(
            sample_lessons, context, max_results=5
        )

        # Hybrid should combine rule + LLM selection
        assert len(results) >= 2
        assert len(results) <= 5


@pytest.fixture
def temp_workspace():
    """Create temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create test files
        (workspace / "git_workflow.py").write_text("# Git workflow code")
        (workspace / "shell_utils.py").write_text("# Shell utilities")
        (workspace / "test_git.py").write_text("# Git tests")
        (workspace / "README.md").write_text("# Project README")

        yield workspace


class TestFileSelectionIntegration:
    """Test file selection with real conversations."""

    @pytest.mark.asyncio
    async def test_rule_based_file_selection(
        self, conversation_about_git, temp_workspace
    ):
        """Test rule-based file selection."""
        config = FileSelectorConfig(strategy="rule")

        # Get files (async even for rule-based)
        result = await select_relevant_files(
            conversation_about_git,
            temp_workspace,
            max_files=5,
            use_selector=False,
            config=config,
        )

        # Should return list of files
        assert isinstance(result, list)
        # Files should exist
        assert all(isinstance(f, Path) for f in result)

    @pytest.mark.slow
    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="Needs more realistic file fixtures with substantial content"
    )
    async def test_llm_based_file_selection(
        self, conversation_about_git, temp_workspace
    ):
        """Test LLM-based file selection."""
        config = FileSelectorConfig(strategy="llm")

        files = await select_relevant_files(
            conversation_about_git,
            temp_workspace,
            max_files=3,
            use_selector=True,
            config=config,
        )

        # LLM should select relevant files
        assert len(files) <= 3
        assert len(files) > 0
        # Files should exist and be from workspace
        assert all(f.exists() for f in files)

    @pytest.mark.slow
    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="Needs more realistic file fixtures with substantial content"
    )
    async def test_hybrid_file_selection(
        self, conversation_mixed_topics, temp_workspace
    ):
        """Test hybrid file selection."""
        config = FileSelectorConfig(strategy="hybrid")

        files = await select_relevant_files(
            conversation_mixed_topics,
            temp_workspace,
            max_files=5,
            use_selector=True,
            config=config,
        )

        # Hybrid should combine rule + LLM
        assert len(files) <= 5
        assert len(files) > 0
        # Files should exist and be from workspace
        assert all(f.exists() for f in files)


class TestPerformanceMetrics:
    """Test performance characteristics of different strategies."""

    @pytest.mark.slow
    def test_rule_based_is_fast(self, conversation_about_git, sample_lessons):
        """Verify rule-based selection is fast (<100ms)."""
        import time

        selector_config = ContextSelectorConfig(strategy="rule")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=False,
        )

        context = messages_to_context(conversation_about_git)

        start = time.time()
        matcher.match(sample_lessons, context)
        duration = time.time() - start

        # Rule-based should be very fast
        assert duration < 0.1  # <100ms

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_llm_based_latency(self, conversation_about_git, sample_lessons):
        """Measure LLM-based selection latency."""
        import time

        selector_config = ContextSelectorConfig(strategy="llm")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=True,
        )

        context = messages_to_context(conversation_about_git)

        start = time.time()
        await matcher.match_with_selector(sample_lessons, context)
        duration = time.time() - start

        # LLM-based will be slower but should be reasonable
        assert duration < 5.0  # <5s for test environment
        # In production with caching, should be much faster


class TestEndToEndWorkflow:
    """Test the complete workflow end-to-end."""

    @pytest.mark.slow
    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="Needs more realistic file fixtures with substantial content"
    )
    async def test_full_context_selection_workflow(
        self, conversation_mixed_topics, sample_lessons, temp_workspace
    ):
        """Test complete workflow: lesson + file selection."""
        # 1. Select lessons
        selector_config = ContextSelectorConfig(strategy="hybrid")
        lesson_config = LessonSelectorConfig()
        lesson_matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=True,
        )
        context = messages_to_context(conversation_mixed_topics)
        lesson_results = await lesson_matcher.match_with_selector(
            sample_lessons, context, max_results=3
        )

        # 2. Select files
        file_config = FileSelectorConfig(strategy="hybrid")
        files = await select_relevant_files(
            conversation_mixed_topics,
            temp_workspace,
            max_files=3,
            use_selector=True,
            config=file_config,
        )

        # 3. Verify results
        assert len(lesson_results) > 0, "Should select at least one lesson"
        assert len(files) > 0, "Should select at least one file"
        assert len(lesson_results) <= 3, "Should respect max_lessons"
        assert len(files) <= 3, "Should respect max_files"

        # 4. Verify quality
        # Lessons should have been matched successfully
        assert all(result.score > 0 for result in lesson_results)
        # Files should exist in workspace
        assert all(f.exists() for f in files)
        assert all(temp_workspace in f.parents for f in files)
