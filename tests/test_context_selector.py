"""Unit tests for context selector module."""

from unittest.mock import Mock, patch

import pytest

from gptme.context_selector import (
    ContextItem,
    ContextSelectorConfig,
    HybridSelector,
    LLMSelector,
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

    def test_keyword_matching(self, sample_items, config):
        """Test basic keyword matching."""
        selector = RuleBasedSelector(config)

        results = selector.select(
            query="How do I use git commit?",
            candidates=sample_items,
            max_results=2,
        )

        assert len(results) <= 2
        assert results[0].identifier in ("item1", "item4")

    def test_case_insensitive(self, sample_items, config):
        """Test case-insensitive matching."""
        selector = RuleBasedSelector(config)

        results = selector.select(
            query="GIT COMMANDS",
            candidates=sample_items,
            max_results=5,
        )

        assert len(results) >= 2
        git_items = [r for r in results if "git" in r.metadata["keywords"]]
        assert len(git_items) >= 2

    def test_priority_boost(self, sample_items, config):
        """Test priority boosting."""
        selector = RuleBasedSelector(config)

        results = selector.select(
            query="git",
            candidates=sample_items,
            max_results=5,
        )

        if len(results) >= 2:
            high_priority_items = [
                r for r in results if r.metadata.get("priority") == "high"
            ]
            assert len(high_priority_items) >= 1

    def test_no_matches(self, sample_items, config):
        """Test behavior when no keywords match."""
        selector = RuleBasedSelector(config)

        results = selector.select(
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


class TestLLMSelector:
    """Tests for LLM-based context selector."""

    @pytest.fixture
    def mock_llm_response(self):
        """Create a mock LLM response."""

        def _mock_response(selected_ids):
            content = "<selected>\n" + "\n".join(selected_ids) + "\n</selected>"
            response = Mock()
            response.content = content
            return response

        return _mock_response

    def test_basic_selection(self, sample_items, mock_llm_response):
        """Test basic LLM selection."""
        config = ContextSelectorConfig(strategy="llm")
        selector = LLMSelector(config)

        # Mock the reply function
        with patch("gptme.llm.reply") as mock_reply:
            mock_reply.return_value = mock_llm_response(["item1", "item3"])

            results = selector.select(
                query="git workflow", candidates=sample_items, max_results=2
            )

        assert len(results) == 2
        assert results[0].identifier == "item1"
        assert results[1].identifier == "item3"

    def test_empty_response(self, sample_items, mock_llm_response):
        """Test handling of empty LLM response."""
        config = ContextSelectorConfig(strategy="llm")
        selector = LLMSelector(config)

        with patch("gptme.llm.reply") as mock_reply:
            mock_reply.return_value = mock_llm_response([])

            results = selector.select(
                query="nonexistent topic", candidates=sample_items, max_results=2
            )

        assert len(results) == 0

    def test_multiple_selected_tags(self, sample_items):
        """Test parsing bug fix: multiple <selected> tags should be handled correctly."""
        config = ContextSelectorConfig(strategy="llm")
        selector = LLMSelector(config)

        # Create response with multiple <selected> tags (edge case)
        response = Mock()
        response.content = """
        Here's my analysis: <selected>
        item1
        item2
        </selected>

        Note: <selected> tags indicate selection.
        """

        with patch("gptme.llm.reply") as mock_reply:
            mock_reply.return_value = response

            results = selector.select(
                query="test", candidates=sample_items, max_results=5
            )

        # Should correctly parse the first <selected> block
        assert len(results) == 2
        assert results[0].identifier == "item1"
        assert results[1].identifier == "item2"

    def test_invalid_identifiers_filtered(self, sample_items, mock_llm_response):
        """Test that invalid identifiers are filtered out."""
        config = ContextSelectorConfig(strategy="llm")
        selector = LLMSelector(config)

        with patch("gptme.llm.reply") as mock_reply:
            # LLM returns mix of valid and invalid IDs
            mock_reply.return_value = mock_llm_response(
                ["item1", "invalid_id", "item3"]
            )

            results = selector.select(
                query="test", candidates=sample_items, max_results=5
            )

        # Should only return valid identifiers
        assert len(results) == 2
        assert all(r.identifier in ["item1", "item3"] for r in results)


class TestHybridSelector:
    """Tests for hybrid context selector."""

    @pytest.fixture
    def mock_llm_response(self):
        """Create a mock LLM response."""

        def _mock_response(selected_ids):
            content = "<selected>\n" + "\n".join(selected_ids) + "\n</selected>"
            response = Mock()
            response.content = content
            return response

        return _mock_response

    def test_short_circuit_path(self, sample_items):
        """Test that hybrid selector short-circuits when pre-filtered <= max_results."""
        config = ContextSelectorConfig(strategy="hybrid")
        selector = HybridSelector(config)

        # With only 3 items and max_results=5, should skip LLM
        with patch("gptme.llm.reply") as mock_reply:
            results = selector.select(
                query="git workflow", candidates=sample_items, max_results=5
            )

            # LLM should not be called (short-circuit)
            mock_reply.assert_not_called()

        assert len(results) <= 5

    def test_llm_refinement_path(self, mock_llm_response):
        """Test that hybrid selector uses LLM when pre-filtered > max_results."""
        # Create more items to trigger LLM refinement
        many_items = [
            SimpleItem(
                identifier=f"item{i}",
                content=f"Content about git workflow {i}",
                metadata={"keywords": ["git", "workflow"], "priority": "medium"},
            )
            for i in range(25)  # 25 items with git keywords
        ]

        config = ContextSelectorConfig(
            strategy="hybrid",
            max_candidates=20,  # Pre-filter to 20 items
            max_selected=5,  # Then refine to 5
        )
        selector = HybridSelector(config)

        with patch("gptme.llm.reply") as mock_reply:
            # Mock LLM to select specific items
            mock_reply.return_value = mock_llm_response([f"item{i}" for i in range(5)])

            results = selector.select(
                query="git workflow best practices",
                candidates=many_items,
                max_results=5,
            )

            # LLM should be called for refinement
            mock_reply.assert_called_once()

        assert len(results) == 5
        assert all(r.identifier.startswith("item") for r in results)

    def test_preserves_llm_selection_order(self, mock_llm_response):
        """Test that hybrid selector preserves LLM's selection order."""
        many_items = [
            SimpleItem(
                identifier=f"item{i}",
                content=f"Content about git workflow {i}",
                metadata={"keywords": ["git", "workflow"], "priority": "medium"},
            )
            for i in range(25)
        ]

        # Increase max_candidates to ensure all items pass pre-filter
        config = ContextSelectorConfig(
            strategy="hybrid",
            max_candidates=30,  # More than 25, so all items included
        )
        selector = HybridSelector(config)

        with patch("gptme.llm.reply") as mock_reply:
            # LLM returns specific order
            mock_reply.return_value = mock_llm_response(
                ["item10", "item5", "item20", "item1", "item15"]
            )

            results = selector.select(
                query="git workflow",  # Match the keywords
                candidates=many_items,
                max_results=5,
            )

        # Order should match LLM's selection
        assert [r.identifier for r in results] == [
            "item10",
            "item5",
            "item20",
            "item1",
            "item15",
        ]
