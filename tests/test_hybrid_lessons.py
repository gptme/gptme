"""Tests for hybrid lesson matching integration."""

import pytest

from gptme.lessons import MatchContext
from gptme.lessons.matcher import LessonMatcher

# Check if hybrid matching is available
try:
    from gptme.lessons.hybrid_matcher import HybridConfig, HybridLessonMatcher

    HYBRID_AVAILABLE = True
except ImportError:
    HYBRID_AVAILABLE = False


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_hybrid_matcher_fallback():
    """Test that HybridLessonMatcher falls back to keyword-only when embeddings unavailable."""
    # This test verifies the fallback mechanism works
    config = HybridConfig(enable_semantic=False)
    matcher = HybridLessonMatcher(config=config)

    # Test with empty lessons list
    context = MatchContext(message="test query")
    results = matcher.match([], context)

    assert isinstance(results, list)
    assert len(results) == 0


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_hybrid_config_defaults():
    """Test that HybridConfig has sensible defaults."""
    config = HybridConfig()

    assert config.keyword_weight == 0.25
    assert config.semantic_weight == 0.40
    assert config.effectiveness_weight == 0.25
    assert config.recency_weight == 0.10
    assert config.tool_bonus == 0.20
    assert config.top_k == 20
    # Phase 5.5: Dynamic top-K parameters
    assert config.min_score_threshold == 0.6
    assert config.min_lessons == 2
    assert config.max_lessons == 10


def test_backward_compatibility():
    """Test that basic LessonMatcher still works (backward compatibility)."""
    matcher = LessonMatcher()
    context = MatchContext(message="test query")
    results = matcher.match([], context)

    assert isinstance(results, list)
    assert len(results) == 0
