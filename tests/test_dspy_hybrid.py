"""Tests for hybrid optimizer implementation (Phase 4.1)."""

import dspy

from gptme.eval.dspy.hybrid_optimizer import (
    HybridOptimizer,
    OptimizationStrategy,
    OptimizerStage,
    TaskComplexity,
    select_optimization_strategy,
)


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


def test_select_optimization_strategy_simple():
    """Test strategy selection for simple tasks."""
    strategy = select_optimization_strategy("SIMPLE", "medium")

    assert len(strategy.stages) == 1
    assert strategy.stages[0] == OptimizerStage.BOOTSTRAP
    assert strategy.complexity == "SIMPLE"
    assert strategy.auto_level == "medium"
    assert strategy.estimated_time_min == 10
    assert strategy.estimated_cost == 0.10


def test_select_optimization_strategy_medium():
    """Test strategy selection for medium tasks."""
    strategy = select_optimization_strategy("MEDIUM", "medium")

    assert len(strategy.stages) == 2
    assert strategy.stages[0] == OptimizerStage.BOOTSTRAP
    assert strategy.stages[1] == OptimizerStage.MIPRO
    assert strategy.complexity == "MEDIUM"
    assert strategy.estimated_time_min == 45
    assert strategy.estimated_cost == 0.50


def test_select_optimization_strategy_complex():
    """Test strategy selection for complex tasks."""
    strategy = select_optimization_strategy("COMPLEX", "medium")

    assert len(strategy.stages) == 3
    assert strategy.stages[0] == OptimizerStage.BOOTSTRAP
    assert strategy.stages[1] == OptimizerStage.MIPRO
    assert strategy.stages[2] == OptimizerStage.GEPA
    assert strategy.complexity == "COMPLEX"
    assert strategy.estimated_time_min == 90
    assert strategy.estimated_cost == 1.30


def test_select_optimization_strategy_light():
    """Test strategy selection with light auto_level."""
    strategy = select_optimization_strategy("COMPLEX", "light")

    assert len(strategy.stages) == 3
    assert strategy.estimated_time_min == 45  # 90 * 0.5
    assert strategy.estimated_cost == 0.65  # 1.30 * 0.5


def test_select_optimization_strategy_heavy():
    """Test strategy selection with heavy auto_level."""
    strategy = select_optimization_strategy("COMPLEX", "heavy")

    assert len(strategy.stages) == 3
    assert strategy.estimated_time_min == 135  # 90 * 1.5
    assert abs(strategy.estimated_cost - 1.95) < 0.01  # 1.30 * 1.5 (floating point)


def test_optimization_strategy_properties():
    """Test OptimizationStrategy properties and string representation."""
    strategy = OptimizationStrategy(
        stages=[OptimizerStage.BOOTSTRAP, OptimizerStage.MIPRO],
        complexity="MEDIUM",
        auto_level="medium",
        estimated_time_min=45,
        estimated_cost=0.50,
    )

    assert strategy.num_stages == 2
    assert "2-stage" in str(strategy)
    assert "Bootstrap → Mipro" in str(strategy)
    assert "45 min" in str(strategy)
    assert "$0.50" in str(strategy)
