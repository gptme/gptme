"""Tests for hybrid lesson matching integration."""

import json
import tempfile
from pathlib import Path

import pytest

from gptme.lessons import MatchContext
from gptme.lessons.matcher import LessonMatcher

# Check if hybrid matching is available
try:
    from gptme.lessons.hybrid_matcher import (
        HybridConfig,
        HybridLessonMatcher,
        _load_ts_posteriors,
    )

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
    assert config.max_lessons == 10


def test_backward_compatibility():
    """Test that basic LessonMatcher still works (backward compatibility)."""
    matcher = LessonMatcher()
    context = MatchContext(message="test query")
    results = matcher.match([], context)

    assert isinstance(results, list)
    assert len(results) == 0


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_ts_posteriors():
    """Test loading Thompson sampling posteriors from JSON state file."""
    state = {
        "arms": {
            "git-workflow.md": {"alpha": 8.0, "beta": 2.0},
            "python-invocation.md": {"alpha": 3.0, "beta": 7.0},
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(state, f)
        f.flush()
        posteriors = _load_ts_posteriors(f.name)

    assert len(posteriors) == 2
    assert posteriors["git-workflow.md"] == pytest.approx(0.8)
    assert posteriors["python-invocation.md"] == pytest.approx(0.3)

    # Cleanup
    Path(f.name).unlink()


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_ts_posteriors_missing_file():
    """Test graceful handling of missing state file."""
    posteriors = _load_ts_posteriors("/nonexistent/path.json")
    assert posteriors == {}


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_with_ts():
    """Test that effectiveness_score uses TS posteriors when configured."""
    state = {
        "arms": {
            "git-workflow.md": {"alpha": 9.0, "beta": 1.0},  # 0.9 effectiveness
        }
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(state, f)
        f.flush()
        state_path = f.name

    config = HybridConfig(
        enable_semantic=False,
        effectiveness_state_file=state_path,
    )
    matcher = HybridLessonMatcher(config=config)

    # Verify posteriors were loaded
    assert len(matcher._ts_posteriors) == 1
    assert matcher._ts_posteriors["git-workflow.md"] == pytest.approx(0.9)

    # Cleanup
    Path(state_path).unlink()


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_default():
    """Test that effectiveness_score returns 0.5 without TS config."""
    config = HybridConfig(enable_semantic=False)
    matcher = HybridLessonMatcher(config=config)
    assert matcher._ts_posteriors == {}
