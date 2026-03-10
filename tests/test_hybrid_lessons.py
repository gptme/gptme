"""Tests for hybrid lesson matching integration."""

import json
from pathlib import Path

import pytest

from gptme.lessons import MatchContext
from gptme.lessons.matcher import LessonMatcher
from gptme.lessons.parser import Lesson, LessonMetadata

# Check if hybrid matching is available
try:
    from gptme.lessons.hybrid_matcher import (
        HybridConfig,
        HybridLessonMatcher,
        _default_effectiveness_state_file,
        _lesson_lookup_keys,
        _load_effectiveness_scores,
        _score_from_judge_arm,
        _score_from_ts_arm,
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
def test_load_effectiveness_scores(tmp_path):
    """Test loading Thompson sampling posteriors from JSON state file."""
    state = {
        "arms": {
            "git-workflow.md": {"alpha": 8.0, "beta": 2.0},
            "python-invocation.md": {"alpha": 3.0, "beta": 7.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))
    posteriors = _load_effectiveness_scores(str(state_file))

    assert len(posteriors) == 2
    assert posteriors["git-workflow.md"] == pytest.approx(0.8)
    assert posteriors["python-invocation.md"] == pytest.approx(0.3)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_missing_file():
    """Test graceful handling of missing state file."""
    posteriors = _load_effectiveness_scores("/nonexistent/path.json")
    assert posteriors == {}


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_zero_total_defaults_to_neutral(tmp_path):
    """Test zero-count arms fall back to neutral 0.5 instead of crashing."""
    state = {
        "arms": {
            "empty-arm.md": {"alpha": 0.0, "beta": 0.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    posteriors = _load_effectiveness_scores(str(state_file))

    assert posteriors == {"empty-arm.md": 0.5}


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_invalid_numeric_data_returns_empty(tmp_path):
    """Test malformed alpha/beta values are handled gracefully."""
    state = {
        "arms": {
            "bad-arm.md": {"alpha": "not-a-number", "beta": 1.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    posteriors = _load_effectiveness_scores(str(state_file))

    assert posteriors == {}


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_score_from_ts_arm_returns_none_without_ts_fields():
    """Judge-only arms should not be misread as TS arms."""
    assert _score_from_ts_arm({"helpful": 3, "harmful": 1}) is None


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_score_from_judge_arm_returns_none_without_judge_fields():
    """TS-only arms should not be misread as judge arms."""
    assert _score_from_judge_arm({"alpha": 2.0, "beta": 1.0}) is None


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_score_from_judge_arm_uses_false_positive_and_noop():
    """Judge score should count both false_positive and noop as non-helpful."""
    arm = {
        "helpful": 3.0,
        "harmful": 1.0,
        "false_positive": 2.0,
        "noop": 4.0,
    }

    # With Laplace smoothing: (helpful+1) / (total+2) = (3+1) / (10+2) = 4/12
    assert _score_from_judge_arm(arm) == pytest.approx(4 / 12)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_non_dict_arm_skipped(tmp_path):
    """Test non-dict arm values (e.g. scalars) are skipped without crashing."""
    state = {
        "arms": {
            "bad-arm.md": 0.9,  # not a dict — would raise TypeError without guard
            "good-arm.md": {"alpha": 8.0, "beta": 2.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    posteriors = _load_effectiveness_scores(str(state_file))

    # bad-arm skipped, good-arm still parsed
    assert "bad-arm.md" not in posteriors
    assert posteriors.get("good-arm.md") == pytest.approx(0.8)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_judge_only_arm(tmp_path):
    """Judge-only state files should still produce lesson effectiveness scores."""
    state = {
        "arms": {
            "workflow/git-workflow.md": {
                "helpful": 6.0,
                "harmful": 2.0,
                "false_positive": 2.0,
            },
        }
    }
    state_file = tmp_path / "judge_state.json"
    state_file.write_text(json.dumps(state))

    posteriors = _load_effectiveness_scores(str(state_file))

    # With Laplace smoothing: (helpful+1) / (total+2) = (6+1) / (10+2) = 7/12
    assert posteriors["workflow/git-workflow.md"] == pytest.approx(7 / 12)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_load_effectiveness_scores_combines_ts_and_judge_scores(tmp_path):
    """Combined state should average session-level TS and lesson-level judge signal."""
    state = {
        "arms": {
            "workflow/git-workflow.md": {
                "alpha": 9.0,
                "beta": 1.0,
                "helpful": 2.0,
                "harmful": 2.0,
                "noop": 2.0,
            },
        }
    }
    state_file = tmp_path / "combined_state.json"
    state_file.write_text(json.dumps(state))

    posteriors = _load_effectiveness_scores(str(state_file))

    # TS = 0.9, judge (with Laplace) = (2+1)/(6+2) = 3/8 = 0.375, combined = avg
    assert posteriors["workflow/git-workflow.md"] == pytest.approx((0.9 + (3 / 8)) / 2)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_with_ts(tmp_path):
    """Test that effectiveness_score uses TS posteriors when configured."""
    state = {
        "arms": {
            "git-workflow.md": {"alpha": 9.0, "beta": 1.0},  # 0.9 effectiveness
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    config = HybridConfig(
        enable_semantic=False,
        effectiveness_state_file=str(state_file),
    )
    matcher = HybridLessonMatcher(config=config)

    # Verify posteriors were loaded
    assert len(matcher._ts_posteriors) == 1
    assert matcher._ts_posteriors["git-workflow.md"] == pytest.approx(0.9)

    # Verify _effectiveness_score uses posteriors via basename lookup
    lesson = Lesson(
        path=Path("/tmp/lessons/git-workflow.md"),
        metadata=LessonMetadata(),
        title="Git Workflow",
        description="",
        category="workflow",
        body="",
    )
    assert matcher._effectiveness_score(lesson) == pytest.approx(0.9)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_matches_short_path(tmp_path):
    """Test lessons can match TS posteriors via category/filename path."""
    state = {
        "arms": {
            "workflow/git-workflow.md": {"alpha": 7.0, "beta": 3.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    matcher = HybridLessonMatcher(
        config=HybridConfig(
            enable_semantic=False,
            effectiveness_state_file=str(state_file),
        )
    )
    lesson = Lesson(
        path=Path("/tmp/lessons/workflow/git-workflow.md"),
        metadata=LessonMetadata(),
        title="Git Workflow",
        description="",
        category="workflow",
        body="",
    )

    assert matcher._effectiveness_score(lesson) == pytest.approx(0.7)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_default(monkeypatch):
    """Test that effectiveness_score returns 0.5 without TS config."""
    monkeypatch.delenv("GPTME_LESSONS_TS_STATE", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent"))
    config = HybridConfig(enable_semantic=False)
    matcher = HybridLessonMatcher(config=config)
    assert matcher._ts_posteriors == {}
    # Verify the fallback path inside _effectiveness_score returns 0.5
    lesson = Lesson(
        path=Path("/tmp/lessons/some-lesson.md"),
        metadata=LessonMetadata(),
        title="Some Lesson",
        description="",
        category="workflow",
        body="",
    )
    assert matcher._effectiveness_score(lesson) == pytest.approx(0.5)


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_default_effectiveness_state_file_uses_env(monkeypatch):
    """Env override should take precedence for TS state discovery."""
    monkeypatch.setenv("GPTME_LESSONS_TS_STATE", "/tmp/custom-bandit.json")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert _default_effectiveness_state_file() == "/tmp/custom-bandit.json"


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_default_effectiveness_state_file_uses_xdg(monkeypatch):
    """XDG state home should be used when env override is absent."""
    monkeypatch.delenv("GPTME_LESSONS_TS_STATE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", "/tmp/xdg-state")

    assert (
        _default_effectiveness_state_file()
        == "/tmp/xdg-state/gptme/lessons/bandit-state.json"
    )


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_lesson_lookup_keys_include_id_and_path_forms():
    """Lookup keys should prefer explicit id and keep legacy path variants."""
    lesson = Lesson(
        path=Path("/tmp/lessons/workflow/git-workflow.md"),
        metadata=LessonMetadata(id="workflow.git-workflow"),
        title="Git Workflow",
        description="",
        category="workflow",
        body="",
    )

    assert _lesson_lookup_keys(lesson) == [
        "workflow.git-workflow",
        "/tmp/lessons/workflow/git-workflow.md",
        "git-workflow.md",
        "workflow/git-workflow.md",
    ]


@pytest.mark.skipif(not HYBRID_AVAILABLE, reason="Hybrid matching not available")
def test_effectiveness_score_matches_explicit_lesson_id(tmp_path):
    """Explicit lesson ids should provide a stable lookup key."""
    state = {
        "arms": {
            "workflow.git-workflow": {"alpha": 8.0, "beta": 2.0},
        }
    }
    state_file = tmp_path / "ts_state.json"
    state_file.write_text(json.dumps(state))

    matcher = HybridLessonMatcher(
        config=HybridConfig(
            enable_semantic=False,
            effectiveness_state_file=str(state_file),
        )
    )
    lesson = Lesson(
        path=Path("/tmp/elsewhere/workflow/git-workflow.md"),
        metadata=LessonMetadata(id="workflow.git-workflow"),
        title="Git Workflow",
        description="",
        category="workflow",
        body="",
    )

    assert matcher._effectiveness_score(lesson) == pytest.approx(0.8)
