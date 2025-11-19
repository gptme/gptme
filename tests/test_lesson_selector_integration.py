"""Tests for lesson context selector integration."""

from pathlib import Path

import pytest

from gptme.context_selector.config import ContextSelectorConfig
from gptme.lessons import (
    EnhancedLessonMatcher,
    Lesson,
    LessonMetadata,
    LessonSelectorConfig,
    MatchContext,
)


def create_test_lesson(
    path: str = "test.md",
    keywords: list[str] | None = None,
    category: str = "general",
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
        title="Test Lesson",
        description="Test description",
        category=category,
        body=content,
    )


class TestLessonItem:
    """Test LessonItem wrapper."""

    def test_lesson_item_content(self):
        """Test that content property returns lesson content."""
        from gptme.lessons.selector_integration import LessonItem

        lesson = create_test_lesson(content="Test content")
        item = LessonItem(lesson)

        assert item.content == "Test content"

    def test_lesson_item_metadata(self):
        """Test that metadata property returns correct dict."""
        from gptme.lessons.selector_integration import LessonItem

        lesson = create_test_lesson(
            keywords=["test", "keyword"],
            category="workflow",
        )
        item = LessonItem(lesson)

        metadata = item.metadata
        assert metadata["keywords"] == ["test", "keyword"]
        assert metadata["priority"] == "normal"  # Default for now
        assert metadata["category"] == "workflow"
        assert metadata["status"] == "active"

    def test_lesson_item_identifier(self):
        """Test identifier extraction from path."""
        from gptme.lessons.selector_integration import LessonItem

        lesson = create_test_lesson(path="/some/path/lessons/workflow/test.md")
        item = LessonItem(lesson)

        # Should extract relative path from lessons directory
        assert item.identifier == "workflow/test.md"


class TestLessonSelectorConfig:
    """Test lesson selector configuration."""

    def test_default_priority_boost(self):
        """Test default priority boost values."""
        config = LessonSelectorConfig()

        assert config.get_priority_boost("critical") == 3.0
        assert config.get_priority_boost("high") == 2.0
        assert config.get_priority_boost("normal") == 1.0
        assert config.get_priority_boost("low") == 0.5

    def test_default_category_weight(self):
        """Test default category weight values."""
        config = LessonSelectorConfig()

        assert config.get_category_weight("workflow") == 1.5
        assert config.get_category_weight("tools") == 1.3
        assert config.get_category_weight("patterns") == 1.2

    def test_apply_metadata_boost(self):
        """Test metadata boost application."""
        config = LessonSelectorConfig()

        # High priority + workflow category
        metadata = {"priority": "high", "category": "workflow"}
        boosted = config.apply_metadata_boost(1.0, metadata)

        # Should be 1.0 * 2.0 (high priority) * 1.5 (workflow) = 3.0
        assert boosted == 3.0

    def test_metadata_boost_with_defaults(self):
        """Test metadata boost with missing fields."""
        config = LessonSelectorConfig()

        # Only priority, no category
        metadata = {"priority": "high"}
        boosted = config.apply_metadata_boost(1.0, metadata)

        # Should be 1.0 * 2.0 (high priority) * 1.0 (default category) = 2.0
        assert boosted == 2.0


class TestEnhancedLessonMatcher:
    """Test enhanced lesson matcher."""

    def test_matcher_initialization(self):
        """Test that matcher initializes correctly."""
        matcher = EnhancedLessonMatcher()

        assert not matcher.use_selector  # Default: False for backward compat
        assert isinstance(matcher.selector_config, ContextSelectorConfig)
        assert isinstance(matcher.lesson_config, LessonSelectorConfig)

    def test_matcher_with_selector_enabled(self):
        """Test that matcher can be created with selector enabled."""
        matcher = EnhancedLessonMatcher(use_selector=True)

        assert matcher.use_selector

    def test_fallback_to_keyword_matching(self):
        """Test that matcher falls back to keyword matching when selector disabled."""
        matcher = EnhancedLessonMatcher(use_selector=False)

        lessons = [
            create_test_lesson(keywords=["git", "workflow"]),
            create_test_lesson(keywords=["python", "coding"]),
        ]

        context = MatchContext(message="I need help with git workflow")
        results = matcher.match(lessons, context)

        # Should use keyword matching
        assert len(results) > 0
        assert results[0].lesson.metadata.keywords == ["git", "workflow"]

    @pytest.mark.asyncio
    async def test_match_with_selector_rule_based(self):
        """Test matching with rule-based selector."""
        selector_config = ContextSelectorConfig(strategy="rule")
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            use_selector=True,
        )

        lessons = [
            create_test_lesson(
                path="workflow/git.md",
                keywords=["git", "workflow"],
                category="workflow",
            ),
            create_test_lesson(
                path="tools/python.md",
                keywords=["python", "coding"],
                category="tools",
            ),
        ]

        context = MatchContext(message="I need help with git workflow")
        results = await matcher.match_with_selector(lessons, context, max_results=2)

        # Should match git lesson with higher category weight
        assert len(results) > 0
        assert results[0].lesson.category == "workflow"

    @pytest.mark.asyncio
    async def test_metadata_boosts_applied(self):
        """Test that metadata boosts are applied to scores."""
        selector_config = ContextSelectorConfig(strategy="rule")
        lesson_config = LessonSelectorConfig()
        matcher = EnhancedLessonMatcher(
            selector_config=selector_config,
            lesson_config=lesson_config,
            use_selector=True,
        )

        lessons = [
            create_test_lesson(
                path="workflow/high-priority.md",
                keywords=["test"],
                category="workflow",
            ),
            create_test_lesson(
                path="tools/normal.md",
                keywords=["test"],
                category="tools",
            ),
        ]

        context = MatchContext(message="test message")
        results = await matcher.match_with_selector(lessons, context, max_results=2)

        # Workflow category (1.5 weight) should score higher than tools (1.3 weight)
        assert results[0].lesson.category == "workflow"
        assert results[0].score > results[1].score
