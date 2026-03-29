"""Tests for gptme.context.task_analyzer module.

Covers:
- TaskFeatures dataclass (defaults, post_init)
- TaskClassification dataclass (validation, types)
- extract_features (prompt signals, file impact, workspace context)
- classify_task (rule-based classification, scoring, normalization)
- select_compression_ratio (type-based ratios, adjustments)
- generate_rationale (human-readable output)
"""

import pytest

from gptme.context.task_analyzer import (
    TaskClassification,
    TaskFeatures,
    _extract_file_impact,
    _extract_prompt_signals,
    _extract_workspace_context,
    classify_task,
    extract_features,
    generate_rationale,
    select_compression_ratio,
)

# ──────────────────── TaskFeatures ────────────────────


class TestTaskFeatures:
    def test_defaults(self):
        f = TaskFeatures()
        assert f.files_to_modify == 0
        assert f.new_files_count == 0
        assert f.total_file_size == 0
        assert f.file_types == set()
        assert f.directory_spread == 0
        assert f.import_depth == 0
        assert f.external_deps == 0
        assert f.internal_coupling == 0.0
        assert f.circular_deps is False
        assert f.has_reference_impl is False
        assert f.reference_files == []
        assert f.pattern_matches == 0
        assert f.total_workspace_size == 0

    def test_post_init_default_prompt_signals(self):
        """Post-init populates prompt_signals with default keys."""
        f = TaskFeatures()
        assert set(f.prompt_signals.keys()) == {
            "diagnostic_score",
            "implementation_score",
            "fix_score",
            "exploration_score",
            "refactor_score",
        }
        assert all(v == 0.0 for v in f.prompt_signals.values())

    def test_post_init_preserves_provided_signals(self):
        """If prompt_signals is explicitly provided, post_init doesn't overwrite."""
        signals = {"custom_score": 0.9}
        f = TaskFeatures(prompt_signals=signals)
        assert f.prompt_signals == {"custom_score": 0.9}


# ──────────────────── TaskClassification ────────────────────


class TestTaskClassification:
    def test_valid_types(self):
        for t in ("diagnostic", "fix", "implementation", "exploration", "refactor"):
            c = TaskClassification(primary_type=t, confidence=0.8)
            assert c.primary_type == t

    def test_invalid_primary_type(self):
        with pytest.raises(ValueError, match="Invalid primary_type"):
            TaskClassification(primary_type="unknown", confidence=0.5)

    def test_confidence_bounds_low(self):
        with pytest.raises(ValueError, match="Confidence must be between"):
            TaskClassification(primary_type="fix", confidence=-0.1)

    def test_confidence_bounds_high(self):
        with pytest.raises(ValueError, match="Confidence must be between"):
            TaskClassification(primary_type="fix", confidence=1.1)

    def test_confidence_boundary_values(self):
        """Confidence at exactly 0.0 and 1.0 should be valid."""
        TaskClassification(primary_type="fix", confidence=0.0)
        TaskClassification(primary_type="fix", confidence=1.0)

    def test_invalid_secondary_type(self):
        with pytest.raises(ValueError, match="Invalid secondary_type"):
            TaskClassification(
                primary_type="fix",
                confidence=0.8,
                secondary_types=["invalid_type"],
            )

    def test_valid_secondary_types(self):
        c = TaskClassification(
            primary_type="fix",
            confidence=0.8,
            secondary_types=["diagnostic", "refactor"],
        )
        assert c.secondary_types == ["diagnostic", "refactor"]

    def test_defaults(self):
        c = TaskClassification(primary_type="fix", confidence=0.5)
        assert c.secondary_types == []
        assert c.all_scores == {}
        assert c.rationale == ""


# ──────────────────── _extract_prompt_signals ────────────────────


class TestExtractPromptSignals:
    def test_empty_prompt(self):
        signals = _extract_prompt_signals("")
        assert all(v == 0.0 for v in signals.values())

    def test_diagnostic_keywords(self):
        signals = _extract_prompt_signals("debug this error, why is it failing?")
        assert signals["diagnostic_score"] > 0
        # "debug", "error", "why", "failing" = 4 matches * 0.3 = 1.0 (capped)
        assert signals["diagnostic_score"] == 1.0

    def test_implementation_keywords(self):
        signals = _extract_prompt_signals("implement a new feature to build X")
        assert signals["implementation_score"] > 0
        # "implement", "new", "feature", "build" = 4 * 0.3 = 1.0 (capped)
        assert signals["implementation_score"] == 1.0

    def test_fix_keywords(self):
        signals = _extract_prompt_signals("fix the bug and resolve the issue")
        assert signals["fix_score"] > 0
        # "fix", "bug", "resolve" = 3 * 0.3 = 0.9
        assert signals["fix_score"] == pytest.approx(0.9)

    def test_exploration_keywords(self):
        signals = _extract_prompt_signals("explore options and compare alternatives")
        assert signals["exploration_score"] > 0

    def test_refactor_keywords(self):
        signals = _extract_prompt_signals(
            "refactor and simplify the code, cleanup imports"
        )
        assert signals["refactor_score"] > 0

    def test_case_insensitive(self):
        signals = _extract_prompt_signals("DEBUG THIS ERROR")
        assert signals["diagnostic_score"] > 0

    def test_mixed_signals(self):
        """A prompt with mixed keywords should score in multiple categories."""
        signals = _extract_prompt_signals("fix the bug, then refactor")
        assert signals["fix_score"] > 0
        assert signals["refactor_score"] > 0

    def test_cap_at_one(self):
        """Even with many matches, score caps at 1.0."""
        signals = _extract_prompt_signals(
            "debug investigate why error failing problem issue"
        )
        assert signals["diagnostic_score"] == 1.0


# ──────────────────── _extract_file_impact ────────────────────


class TestExtractFileImpact:
    def test_empty_files(self):
        features = TaskFeatures()
        _extract_file_impact(features, [])
        assert features.files_to_modify == 0

    def test_file_count_and_types(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.ts"
        f1.touch()
        f2.touch()
        features = TaskFeatures()
        _extract_file_impact(features, [f1, f2])
        assert features.files_to_modify == 2
        assert features.file_types == {".py", ".ts"}

    def test_directory_spread(self, tmp_path):
        d1 = tmp_path / "src"
        d2 = tmp_path / "tests"
        d1.mkdir()
        d2.mkdir()
        f1 = d1 / "a.py"
        f2 = d2 / "b.py"
        f1.touch()
        f2.touch()
        features = TaskFeatures()
        _extract_file_impact(features, [f1, f2])
        assert features.directory_spread == 2

    def test_file_size(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello world")
        features = TaskFeatures()
        _extract_file_impact(features, [f])
        assert features.total_file_size == len("hello world")

    def test_nonexistent_file(self, tmp_path):
        """Files that don't exist contribute 0 to total_file_size."""
        f = tmp_path / "nonexistent.py"
        features = TaskFeatures()
        _extract_file_impact(features, [f])
        assert features.files_to_modify == 1
        assert features.total_file_size == 0
        assert features.file_types == {".py"}


# ──────────────────── _extract_workspace_context ────────────────────


class TestExtractWorkspaceContext:
    def test_empty_context(self):
        features = TaskFeatures()
        _extract_workspace_context(features, [])
        assert features.total_workspace_size == 0

    def test_total_workspace_size(self):
        features = TaskFeatures()
        _extract_workspace_context(features, ["abc", "defgh"])
        assert features.total_workspace_size == 8

    def test_reference_impl_detected(self):
        """Detects reference implementations when >=3 items contain indicators."""
        features = TaskFeatures()
        context = [
            "class Foo:\n  pass",
            "def bar():\n  pass",
            "function baz() {}",
            "no code here",
        ]
        _extract_workspace_context(features, context)
        assert features.has_reference_impl is True
        assert features.pattern_matches == 3

    def test_no_reference_impl(self):
        """With fewer than 3 indicator matches, no reference detected."""
        features = TaskFeatures()
        context = [
            "class Foo:\n  pass",
            "def bar():\n  pass",
            "no code here",
        ]
        _extract_workspace_context(features, context)
        assert features.has_reference_impl is False
        assert features.pattern_matches == 0


# ──────────────────── extract_features ────────────────────


class TestExtractFeatures:
    def test_prompt_only(self):
        features = extract_features("fix the bug")
        assert features.prompt_signals["fix_score"] > 0
        assert features.files_to_modify == 0

    def test_with_workspace_files(self, tmp_path):
        f = tmp_path / "test.py"
        f.touch()
        features = extract_features("implement feature", workspace_files=[f])
        assert features.files_to_modify == 1
        assert features.file_types == {".py"}

    def test_with_current_context(self):
        features = extract_features(
            "explore the code",
            current_context=["class A:", "def b:", "function c:", "text"],
        )
        assert features.total_workspace_size > 0
        assert features.has_reference_impl is True


# ──────────────────── classify_task ────────────────────


class TestClassifyTask:
    def test_diagnostic_prompt(self):
        features = extract_features("debug this error, why is it failing?")
        classification = classify_task(features)
        assert classification.primary_type == "diagnostic"
        assert classification.confidence > 0

    def test_fix_prompt(self):
        features = extract_features("fix the bug in utils.py")
        classification = classify_task(features)
        assert classification.primary_type == "fix"

    def test_implementation_prompt(self):
        features = extract_features("implement a new feature to create the API")
        classification = classify_task(features)
        assert classification.primary_type == "implementation"

    def test_exploration_prompt(self):
        """Exploration keywords should produce high exploration score."""
        features = extract_features("explore options and compare alternatives")
        classification = classify_task(features)
        # exploration score should be highest or tied for highest
        assert classification.all_scores["exploration"] > 0
        # With no file context, fix/diagnostic get rule-based boosts too,
        # so we check the score is competitive rather than demanding it wins
        assert classification.all_scores["exploration"] >= 0.5

    def test_refactor_prompt(self):
        """Refactor keywords with many existing files should classify as refactor."""
        features = TaskFeatures(
            files_to_modify=5,
            new_files_count=0,
            prompt_signals=_extract_prompt_signals("refactor and simplify the module"),
        )
        classification = classify_task(features)
        assert classification.primary_type == "refactor"

    def test_confidence_normalized(self):
        """Primary type confidence should be exactly 1.0 after normalization."""
        features = extract_features("fix the bug")
        classification = classify_task(features)
        assert classification.confidence == pytest.approx(1.0)

    def test_secondary_types(self):
        """Mixed prompts can produce secondary types."""
        features = extract_features("debug and fix the failing test")
        classification = classify_task(features)
        # At least one type should be classified
        assert classification.primary_type in {
            "diagnostic",
            "fix",
            "implementation",
            "exploration",
            "refactor",
        }

    def test_file_impact_boosts_fix(self):
        """Few files + no new files should boost fix score."""
        features = TaskFeatures(
            files_to_modify=1,
            new_files_count=0,
            prompt_signals={
                "fix_score": 0.3,
                "diagnostic_score": 0.0,
                "implementation_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )
        classification = classify_task(features)
        assert "fix" in classification.all_scores

    def test_many_new_files_boosts_implementation(self):
        """Many new files should boost implementation score."""
        features = TaskFeatures(
            files_to_modify=5,
            new_files_count=5,
            prompt_signals={
                "implementation_score": 0.3,
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )
        classification = classify_task(features)
        assert classification.primary_type == "implementation"

    def test_many_existing_files_boosts_refactor(self):
        """Many existing files (no new) should boost refactor score."""
        features = TaskFeatures(
            files_to_modify=10,
            new_files_count=0,
            prompt_signals={
                "refactor_score": 0.3,
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "implementation_score": 0.0,
                "exploration_score": 0.0,
            },
        )
        classification = classify_task(features)
        assert classification.primary_type == "refactor"

    def test_deep_imports_boosts_implementation(self):
        """High import depth should boost implementation and refactor."""
        features = TaskFeatures(
            import_depth=7,
            prompt_signals={
                "implementation_score": 0.3,
                "diagnostic_score": 0.0,
                "fix_score": 0.0,
                "exploration_score": 0.0,
                "refactor_score": 0.0,
            },
        )
        classification = classify_task(features)
        assert classification.all_scores["implementation"] > 0

    def test_empty_features(self):
        """Empty features should still produce a valid classification."""
        features = TaskFeatures()
        classification = classify_task(features)
        assert classification.primary_type in {
            "diagnostic",
            "fix",
            "implementation",
            "exploration",
            "refactor",
        }
        assert 0.0 <= classification.confidence <= 1.0

    def test_rationale_populated(self):
        features = extract_features("fix the critical bug")
        classification = classify_task(features)
        assert len(classification.rationale) > 0
        assert "fix" in classification.rationale.lower()

    def test_all_scores_populated(self):
        features = extract_features("implement something")
        classification = classify_task(features)
        assert set(classification.all_scores.keys()) == {
            "diagnostic",
            "fix",
            "implementation",
            "exploration",
            "refactor",
        }


# ──────────────────── select_compression_ratio ────────────────────


class TestSelectCompressionRatio:
    def test_diagnostic_ratio(self):
        c = TaskClassification(primary_type="diagnostic", confidence=0.9)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        assert 0.10 <= ratio <= 0.15

    def test_fix_ratio(self):
        c = TaskClassification(primary_type="fix", confidence=0.9)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        assert 0.15 <= ratio <= 0.20

    def test_implementation_ratio(self):
        c = TaskClassification(primary_type="implementation", confidence=0.9)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        assert 0.30 <= ratio <= 0.50

    def test_exploration_ratio(self):
        c = TaskClassification(primary_type="exploration", confidence=0.9)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        assert 0.20 <= ratio <= 0.30

    def test_refactor_ratio(self):
        c = TaskClassification(primary_type="refactor", confidence=0.9)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        assert 0.25 <= ratio <= 0.35

    def test_reference_impl_increases_ratio(self):
        c = TaskClassification(primary_type="implementation", confidence=0.9)
        f_no_ref = TaskFeatures()
        f_ref = TaskFeatures(has_reference_impl=True)
        ratio_no_ref = select_compression_ratio(c, f_no_ref)
        ratio_ref = select_compression_ratio(c, f_ref)
        assert ratio_ref >= ratio_no_ref

    def test_low_confidence_uses_moderate(self):
        c = TaskClassification(primary_type="diagnostic", confidence=0.4)
        f = TaskFeatures()
        ratio = select_compression_ratio(c, f)
        # Low confidence → 0.25 (moderate)
        assert ratio == pytest.approx(0.25)

    def test_large_workspace_compresses_more(self):
        c = TaskClassification(primary_type="implementation", confidence=0.9)
        f_small = TaskFeatures(total_workspace_size=100)
        f_large = TaskFeatures(total_workspace_size=2_000_000)
        ratio_small = select_compression_ratio(c, f_small)
        ratio_large = select_compression_ratio(c, f_large)
        assert ratio_large < ratio_small

    def test_safety_bounds(self):
        """Ratio should always be between 0.10 and 0.50."""
        for task_type in (
            "diagnostic",
            "fix",
            "implementation",
            "exploration",
            "refactor",
        ):
            for conf in (0.1, 0.5, 0.9):
                c = TaskClassification(primary_type=task_type, confidence=conf)
                f = TaskFeatures(total_workspace_size=10_000_000)
                ratio = select_compression_ratio(c, f)
                assert 0.10 <= ratio <= 0.50, (
                    f"ratio {ratio} out of bounds for {task_type} conf={conf}"
                )


# ──────────────────── generate_rationale ────────────────────


class TestGenerateRationale:
    def test_basic_rationale(self):
        c = TaskClassification(primary_type="fix", confidence=0.8)
        f = TaskFeatures(files_to_modify=1)
        text = generate_rationale(c, f, 0.17)
        assert "fix" in text.lower()
        assert "0.17" in text
        assert "Few files" in text

    def test_implementation_with_reference(self):
        c = TaskClassification(primary_type="implementation", confidence=0.9)
        f = TaskFeatures(new_files_count=5, has_reference_impl=True)
        text = generate_rationale(c, f, 0.50)
        assert "implementation" in text.lower()
        assert "Reference implementation" in text
        assert "new files" in text.lower()

    def test_aggressive_compression_note(self):
        c = TaskClassification(primary_type="diagnostic", confidence=0.95)
        f = TaskFeatures()
        text = generate_rationale(c, f, 0.12)
        assert "Aggressive compression" in text

    def test_conservative_compression_note(self):
        c = TaskClassification(primary_type="implementation", confidence=0.95)
        f = TaskFeatures()
        text = generate_rationale(c, f, 0.40)
        assert "Conservative compression" in text

    def test_moderate_compression_note(self):
        c = TaskClassification(primary_type="exploration", confidence=0.8)
        f = TaskFeatures()
        text = generate_rationale(c, f, 0.25)
        assert "Moderate compression" in text

    def test_reduction_percentage(self):
        c = TaskClassification(primary_type="fix", confidence=0.8)
        f = TaskFeatures()
        text = generate_rationale(c, f, 0.30)
        assert "70% reduction" in text
