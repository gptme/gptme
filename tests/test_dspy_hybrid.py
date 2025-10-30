"""Tests for hybrid optimizer implementation (Phase 4.1)."""

import dspy

from gptme.eval.dspy.hybrid_optimizer import HybridOptimizer, TaskComplexity


def test_task_complexity_simple():
    """Test complexity analysis for simple tasks."""
    example = dspy.Example(task_description="Hello", context="World")
    assert TaskComplexity.analyze(example) == TaskComplexity.SIMPLE


def test_task_complexity_medium():
    """Test complexity analysis for medium tasks."""
    example = dspy.Example(
        task_description="A" * 150,
        context="B" * 150,
    )
    assert TaskComplexity.analyze(example) == TaskComplexity.MEDIUM


def test_task_complexity_complex():
    """Test complexity analysis for complex tasks."""
    example = dspy.Example(
        task_description="A" * 600,
        context="B" * 600,
    )
    assert TaskComplexity.analyze(example) == TaskComplexity.COMPLEX


def test_hybrid_optimizer_initialization():
    """Test HybridOptimizer can be initialized."""

    def dummy_metric(gold, pred, trace=None):
        return 1.0

    optimizer = HybridOptimizer(
        metric=dummy_metric,
        max_demos=3,
        num_trials=5,
    )

    assert optimizer.metric == dummy_metric
    assert optimizer.max_demos == 3
    assert optimizer.num_trials == 5
    assert optimizer.auto_stage == "medium"


def test_trainset_complexity_analysis():
    """Test overall trainset complexity analysis."""

    def dummy_metric(gold, pred, trace=None):
        return 1.0

    optimizer = HybridOptimizer(metric=dummy_metric)

    # Simple trainset
    simple_trainset = [
        dspy.Example(task_description="A" * 50, context="B" * 50) for _ in range(5)
    ]
    assert (
        optimizer._analyze_trainset_complexity(simple_trainset) == TaskComplexity.SIMPLE
    )

    # Complex trainset
    complex_trainset = [
        dspy.Example(task_description="A" * 600, context="B" * 600) for _ in range(5)
    ]
    assert (
        optimizer._analyze_trainset_complexity(complex_trainset)
        == TaskComplexity.COMPLEX
    )

    # Mixed trainset (should be medium)
    mixed_trainset = [
        dspy.Example(task_description="A" * 50, context="B" * 50),  # simple
        dspy.Example(task_description="A" * 300, context="B" * 300),  # medium
        dspy.Example(task_description="A" * 600, context="B" * 600),  # complex
    ]
    assert (
        optimizer._analyze_trainset_complexity(mixed_trainset) == TaskComplexity.MEDIUM
    )
