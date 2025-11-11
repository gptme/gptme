"""Unit tests for context selector module."""

import pytest

from gptme.context_selector import (
    ContextItem,
    ContextSelectorConfig,
    RuleBasedSelector,
)


class SimpleItem(ContextItem):
    """Simple concrete implementation for testing."""

    def __init__(self, identifier: str, content: str, metadata: dict):
        self._identifier = identifier
        self._content = content
        self._metadata = metadata

    @property
    def content(self) -> str:
        return self._content

    @property
    def metadata(self) -> dict:
        return self._metadata

    @property
    def identifier(self) -> str:
        return self._identifier


@pytest.fixture
def sample_items():
    """Create sample items for testing."""
    return [
        SimpleItem(
            identifier="item1",
            content="This is about git workflow and commits",
            metadata={"keywords": ["git", "commit", "workflow"], "priority": "high"},
        ),
        SimpleItem(
            identifier="item2",
            content="Information about shell commands",
            metadata={"keywords": ["shell", "command"], "priority": "medium"},
        ),
        SimpleItem(
            identifier="item3",
            content="Details about patch tool usage",
            metadata={"keywords": ["patch", "tool"], "priority": "low"},
        ),
        SimpleItem(
            identifier="item4",
            content="More about git branches",
            metadata={"keywords": ["git", "branch"], "priority": "high"},
        ),
    ]


@pytest.fixture
def config():
    """Create test configuration."""
    return ContextSelectorConfig(
        strategy="hybrid",
        max_candidates=20,
        max_selected=5,
    )


class TestRuleBasedSelector:
    """Tests for RuleBasedSelector."""

    @pytest.mark.asyncio
    async def test_keyword_matching(self, sample_items, config):
        """Test basic keyword matching."""
        selector = RuleBasedSelector(config)

        results = await selector.select(
            query="How do I use git commit?",
            candidates=sample_items,
            max_results=2,
        )

        assert len(results) <= 2
        assert results[0].identifier in ("item1", "item4")

    @pytest.mark.asyncio
    async def test_case_insensitive(self, sample_items, config):
        """Test case-insensitive matching."""
        selector = RuleBasedSelector(config)

        results = await selector.select(
            query="GIT COMMANDS",
            candidates=sample_items,
            max_results=5,
        )

        assert len(results) >= 2
        git_items = [r for r in results if "git" in r.metadata["keywords"]]
        assert len(git_items) >= 2

    @pytest.mark.asyncio
    async def test_priority_boost(self, sample_items, config):
        """Test priority boosting."""
        selector = RuleBasedSelector(config)

        results = await selector.select(
            query="git",
            candidates=sample_items,
            max_results=5,
        )

        if len(results) >= 2:
            high_priority_items = [
                r for r in results if r.metadata.get("priority") == "high"
            ]
            assert len(high_priority_items) >= 1

    @pytest.mark.asyncio
    async def test_no_matches(self, sample_items, config):
        """Test behavior when no keywords match."""
        selector = RuleBasedSelector(config)

        results = await selector.select(
            query="python programming",
            candidates=sample_items,
            max_results=5,
        )

        assert len(results) == 0


class TestContextSelectorConfig:
    """Tests for ContextSelectorConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = ContextSelectorConfig()

        assert config.enabled is True
        assert config.strategy == "hybrid"
        assert config.llm_model == "openai/gpt-4o-mini"
        assert config.max_candidates == 20
        assert config.max_selected == 5

    def test_from_dict(self):
        """Test configuration from dictionary."""
        config_dict = {
            "strategy": "rule",
            "max_selected": 10,
        }

        config = ContextSelectorConfig.from_dict(config_dict)

        assert config.strategy == "rule"
        assert config.max_selected == 10
        assert config.enabled is True

    def test_priority_boost(self):
        """Test priority boost configuration."""
        config = ContextSelectorConfig()

        assert "high" in config.lesson_priority_boost
        assert "critical" in config.lesson_priority_boost
        assert config.lesson_priority_boost["high"] == 2.0
        assert config.lesson_priority_boost["critical"] == 3.0


class TestSimpleItem:
    """Tests for SimpleItem implementation."""

    def test_properties(self):
        """Test that properties work correctly."""
        item = SimpleItem(
            identifier="test1",
            content="Test content",
            metadata={"key": "value"},
        )

        assert item.identifier == "test1"
        assert item.content == "Test content"
        assert item.metadata == {"key": "value"}
