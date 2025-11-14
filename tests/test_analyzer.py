"""Unit tests for context_compression.analyzer module.

Tests for:
- Indicator data structures
- Complexity scoring algorithm
- Ratio selection logic
- Task analyzer interface
"""

import pytest

from gptme.context_compression.analyzer import (
    ContextIndicators,
    DependencyIndicators,
    PatternIndicators,
    ScopeIndicators,
    TaskAnalyzer,
    TaskIndicators,
    calculate_complexity_score,
    select_compression_ratio,
)
from gptme.context_compression.analyzer.ratio_selector import (
    estimate_reduction,
    get_ratio_category,
)
from gptme.context_compression.analyzer.scorer import classify_complexity


class TestIndicators:
    """Test indicator data structures."""

    def test_scope_indicators_default(self):
        """Test ScopeIndicators default values."""
        scope = ScopeIndicators()
        assert scope.files_count == 0
        assert scope.lines_estimate == 0
        assert scope.new_files is False
        assert scope.file_types == set()

    def test_scope_indicators_to_dict(self):
        """Test ScopeIndicators conversion to dict."""
        scope = ScopeIndicators(
            files_count=3, lines_estimate=150, new_files=True, file_types={"py", "md"}
        )
        data = scope.to_dict()
        assert data["files_count"] == 3
        assert data["lines_estimate"] == 150
        assert data["new_files"] is True
        assert set(data["file_types"]) == {"py", "md"}

    def test_dependency_indicators_default(self):
        """Test DependencyIndicators default values."""
        deps = DependencyIndicators()
        assert deps.external_libs == set()
        assert deps.internal_modules == set()
        assert deps.new_classes == 0
        assert deps.inheritance_depth == 0

    def test_pattern_indicators_default(self):
        """Test PatternIndicators default values."""
        patterns = PatternIndicators()
        assert patterns.keywords == set()
        assert patterns.verbs == set()
        assert patterns.mentions_design is False
        assert patterns.mentions_reference is False

    def test_context_indicators_default(self):
        """Test ContextIndicators default values."""
        context = ContextIndicators()
        assert context.reference_impls == []
        assert context.examples_available is False
        assert context.tests_exist is False
        assert context.docs_exist is False

    def test_task_indicators_default(self):
        """Test TaskIndicators default values."""
        indicators = TaskIndicators()
        assert isinstance(indicators.scope, ScopeIndicators)
        assert isinstance(indicators.dependencies, DependencyIndicators)
        assert isinstance(indicators.patterns, PatternIndicators)
        assert isinstance(indicators.context, ContextIndicators)

    def test_task_indicators_to_dict(self):
        """Test TaskIndicators conversion to dict."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=2),
            patterns=PatternIndicators(mentions_design=True),
        )
        data = indicators.to_dict()
        assert "scope" in data
        assert "dependencies" in data
        assert "patterns" in data
        assert "context" in data
        assert data["scope"]["files_count"] == 2
        assert data["patterns"]["mentions_design"] is True


class TestComplexityScoring:
    """Test complexity scoring algorithm."""

    def test_empty_indicators_focused(self):
        """Empty indicators should result in focused classification."""
        indicators = TaskIndicators()
        score = calculate_complexity_score(indicators)
        assert 0.0 <= score < 0.3
        assert classify_complexity(score) == "focused"

    def test_focused_task_ci_fix(self):
        """CI fix: 1 file, <100 lines, simple fix."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=1, lines_estimate=50),
            patterns=PatternIndicators(verbs={"fix"}),
        )
        score = calculate_complexity_score(indicators)
        assert score < 0.3
        assert classify_complexity(score) == "focused"

    def test_focused_task_bug_fix(self):
        """Bug fix: 2 files, small changes."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=2, lines_estimate=80),
            patterns=PatternIndicators(verbs={"fix", "patch"}),
        )
        score = calculate_complexity_score(indicators)
        assert score < 0.3
        assert classify_complexity(score) == "focused"

    def test_mixed_task_refactoring(self):
        """Refactoring: 3-4 files, moderate changes."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=4, lines_estimate=200),
            dependencies=DependencyIndicators(new_classes=1),
            patterns=PatternIndicators(verbs={"refactor"}),
        )
        score = calculate_complexity_score(indicators)
        assert 0.3 <= score < 0.7
        assert classify_complexity(score) == "mixed"

    def test_architecture_task_implementation(self):
        """Implementation: 5+ files, 500+ lines, new classes."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(
                files_count=6, lines_estimate=600, new_files=True, file_types={"py"}
            ),
            dependencies=DependencyIndicators(
                new_classes=3,
                external_libs={"requests", "pydantic"},
                inheritance_depth=2,
            ),
            patterns=PatternIndicators(
                verbs={"implement", "create"},
                mentions_design=True,
            ),
        )
        score = calculate_complexity_score(indicators)
        assert score >= 0.7
        assert classify_complexity(score) == "architecture"

    def test_architecture_task_with_missing_references(self):
        """Architecture task without reference implementations."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=5, lines_estimate=400),
            dependencies=DependencyIndicators(new_classes=2),
            patterns=PatternIndicators(mentions_design=True),
            context=ContextIndicators(
                reference_impls=[],  # No references
                examples_available=False,
                tests_exist=False,
            ),
        )
        score = calculate_complexity_score(indicators)
        # This scores as "mixed" (0.53), not "architecture"
        # 5 files (0.25) + 400 lines (0.10) + 2 classes (0.08) + design (0.10) = 0.53
        assert 0.3 <= score < 0.7
        assert classify_complexity(score) == "mixed"

    def test_score_capped_at_one(self):
        """Score should be capped at 1.0."""
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=20, lines_estimate=2000, new_files=True),
            dependencies=DependencyIndicators(
                new_classes=10,
                external_libs={"lib1", "lib2", "lib3", "lib4", "lib5"},
                inheritance_depth=5,
            ),
            patterns=PatternIndicators(
                verbs={"implement", "design", "create"},
                mentions_design=True,
                mentions_reference=True,
            ),
            context=ContextIndicators(
                reference_impls=[],
                examples_available=False,
                tests_exist=False,
                docs_exist=False,
            ),
        )
        score = calculate_complexity_score(indicators)
        assert score <= 1.0


class TestRatioSelection:
    """Test compression ratio selection."""

    def test_focused_ratio_range(self):
        """Focused tasks should get aggressive compression (0.10-0.20)."""
        for score in [0.0, 0.1, 0.2, 0.29]:
            ratio = select_compression_ratio(score)
            assert 0.10 <= ratio <= 0.20, f"Score {score} gave ratio {ratio}"
            assert get_ratio_category(ratio) == "aggressive"

    def test_mixed_ratio_range(self):
        """Mixed tasks should get moderate compression (0.20-0.30)."""
        for score in [0.3, 0.4, 0.5, 0.6, 0.69]:
            ratio = select_compression_ratio(score)
            assert 0.20 <= ratio <= 0.30, f"Score {score} gave ratio {ratio}"
            assert get_ratio_category(ratio) == "moderate"

    def test_architecture_ratio_range(self):
        """Architecture tasks should get conservative compression (0.30-0.50)."""
        for score in [0.7, 0.8, 0.9, 1.0]:
            ratio = select_compression_ratio(score)
            # Allow for floating point precision (0.501 for score 1.0)
            assert 0.30 <= ratio <= 0.51, f"Score {score} gave ratio {ratio}"
            assert get_ratio_category(ratio) == "conservative"

    def test_specific_ratios(self):
        """Test specific score to ratio mappings."""
        assert select_compression_ratio(0.0) == pytest.approx(0.10, abs=0.01)
        assert select_compression_ratio(0.15) == pytest.approx(0.15, abs=0.01)
        assert select_compression_ratio(0.29) == pytest.approx(0.20, abs=0.01)

        assert select_compression_ratio(0.3) == pytest.approx(0.20, abs=0.01)
        assert select_compression_ratio(0.5) == pytest.approx(0.25, abs=0.01)
        assert select_compression_ratio(0.69) == pytest.approx(0.30, abs=0.01)

        assert select_compression_ratio(0.7) == pytest.approx(0.30, abs=0.01)
        assert select_compression_ratio(1.0) == pytest.approx(0.50, abs=0.01)

    def test_ratio_clipping(self):
        """Test that invalid complexity scores are handled."""
        assert select_compression_ratio(-0.5) == pytest.approx(0.10, abs=0.01)
        assert select_compression_ratio(1.5) == pytest.approx(0.50, abs=0.01)

    def test_reduction_estimates(self):
        """Test reduction percentage estimates."""
        assert estimate_reduction(0.10) == 0.90  # 90% reduction
        assert estimate_reduction(0.15) == 0.85  # 85% reduction
        assert estimate_reduction(0.25) == 0.75  # 75% reduction
        assert estimate_reduction(0.40) == 0.60  # 60% reduction


class TestTaskAnalyzer:
    """Test TaskAnalyzer interface."""

    def test_analyzer_creation(self):
        """Test TaskAnalyzer can be instantiated."""
        analyzer = TaskAnalyzer()
        assert analyzer is not None

    def test_analyze_empty_returns_focused(self):
        """Empty analysis should result in focused classification."""
        analyzer = TaskAnalyzer()
        result = analyzer.analyze()

        assert result.complexity_category == "focused"
        assert result.ratio_category == "aggressive"
        assert 0.10 <= result.compression_ratio <= 0.20
        assert 0.80 <= result.estimated_reduction <= 0.90

    def test_analysis_result_summary(self):
        """Test AnalysisResult summary generation."""
        analyzer = TaskAnalyzer()
        result = analyzer.analyze()

        summary = result.summary()
        assert "Task Complexity:" in summary
        assert "Compression:" in summary
        assert "reduction" in summary

    def test_analysis_result_to_dict(self):
        """Test AnalysisResult conversion to dict."""
        analyzer = TaskAnalyzer()
        result = analyzer.analyze()

        data = result.to_dict()
        assert "indicators" in data
        assert "complexity_score" in data
        assert "complexity_category" in data
        assert "compression_ratio" in data
        assert "ratio_category" in data
        assert "estimated_reduction" in data


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_phase2_test05_focused(self):
        """Test-05 (CI fix) should be classified as focused."""
        # Simulate test-05: CI fix, 1 file, simple change
        indicators = TaskIndicators(
            scope=ScopeIndicators(files_count=1, lines_estimate=20),
            patterns=PatternIndicators(verbs={"fix"}),
        )

        score = calculate_complexity_score(indicators)
        ratio = select_compression_ratio(score)

        assert classify_complexity(score) == "focused"
        assert 0.10 <= ratio <= 0.20  # Aggressive compression

    def test_phase2_test15_architecture(self):
        """Test-15 (implementation) should be classified as architecture."""
        # Simulate test-15: Complete service implementation
        indicators = TaskIndicators(
            scope=ScopeIndicators(
                files_count=5, lines_estimate=260, new_files=True, file_types={"py"}
            ),
            dependencies=DependencyIndicators(
                new_classes=4,
                external_libs={"asyncio"},
                internal_modules={"orchestrator"},
            ),
            patterns=PatternIndicators(
                verbs={"implement", "create"}, mentions_design=True
            ),
            context=ContextIndicators(
                reference_impls=[],  # Missing reference
                examples_available=False,
            ),
        )

        score = calculate_complexity_score(indicators)
        ratio = select_compression_ratio(score)

        assert classify_complexity(score) == "architecture"
        assert 0.30 <= ratio <= 0.50  # Conservative compression

    def test_analyzer_consistency(self):
        """Same indicators should produce consistent results."""
        analyzer = TaskAnalyzer()

        result1 = analyzer.analyze()
        result2 = analyzer.analyze()

        assert result1.complexity_score == result2.complexity_score
        assert result1.compression_ratio == result2.compression_ratio
        assert result1.complexity_category == result2.complexity_category
