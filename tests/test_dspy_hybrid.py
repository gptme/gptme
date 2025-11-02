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


def test_budget_constraint_complex_to_medium():
    """Test budget constraint downgrades COMPLEX to MEDIUM."""
    # COMPLEX normally costs $1.30, but budget is $0.90
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=0.90, time_limit_min=None
    )

    # Should downgrade to 2-stage (Bootstrap + MIPROv2)
    assert len(strategy.stages) == 2
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    assert OptimizerStage.MIPRO in strategy.stages
    assert OptimizerStage.GEPA not in strategy.stages
    # Cost should be ~$0.78 (1.30 * 0.6)
    assert strategy.estimated_cost < 0.90


def test_budget_constraint_complex_to_simple():
    """Test budget constraint downgrades COMPLEX to SIMPLE."""
    # COMPLEX normally costs $1.30, but budget is $0.15
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=0.15, time_limit_min=None
    )

    # Should downgrade to 1-stage (Bootstrap only)
    assert len(strategy.stages) == 1
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    assert OptimizerStage.MIPRO not in strategy.stages
    # Cost should be ~$0.16 (1.30 * 0.6 * 0.2) or similar
    assert strategy.estimated_cost < 0.20


def test_budget_constraint_medium_to_simple():
    """Test budget constraint downgrades MEDIUM to SIMPLE."""
    # MEDIUM normally costs $0.50, but budget is $0.15
    strategy = select_optimization_strategy(
        "MEDIUM", "medium", budget_limit=0.15, time_limit_min=None
    )

    # Should downgrade to 1-stage (Bootstrap only)
    assert len(strategy.stages) == 1
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    # Cost should be ~$0.10 (0.50 * 0.2)
    assert strategy.estimated_cost < 0.15


def test_time_constraint_complex_to_medium():
    """Test time constraint downgrades COMPLEX to MEDIUM."""
    # COMPLEX normally takes 90 min, but limit is 60 min
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=None, time_limit_min=60
    )

    # Should downgrade to 2-stage (Bootstrap + MIPROv2)
    assert len(strategy.stages) == 2
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    assert OptimizerStage.MIPRO in strategy.stages
    assert OptimizerStage.GEPA not in strategy.stages
    # Time should be ~45 min (90 * 0.5)
    assert strategy.estimated_time_min <= 60


def test_time_constraint_complex_to_simple():
    """Test time constraint downgrades COMPLEX to SIMPLE."""
    # COMPLEX normally takes 90 min, but limit is 12 min
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=None, time_limit_min=12
    )

    # Should downgrade to 1-stage (Bootstrap only)
    assert len(strategy.stages) == 1
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    # Time should be ~10 min (90 * 0.5 * 0.22) or similar
    assert strategy.estimated_time_min <= 12


def test_both_constraints_together():
    """Test both budget and time constraints together."""
    # COMPLEX with tight budget AND time
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=0.15, time_limit_min=15
    )

    # Should downgrade to 1-stage (Bootstrap only)
    assert len(strategy.stages) == 1
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    assert strategy.estimated_cost < 0.20
    assert strategy.estimated_time_min <= 15


def test_no_downgrade_when_within_constraints():
    """Test no downgrade happens when strategy is within constraints."""
    # COMPLEX with generous constraints
    strategy = select_optimization_strategy(
        "COMPLEX", "medium", budget_limit=2.0, time_limit_min=120
    )

    # Should keep all 3 stages
    assert len(strategy.stages) == 3
    assert OptimizerStage.BOOTSTRAP in strategy.stages
    assert OptimizerStage.MIPRO in strategy.stages
    assert OptimizerStage.GEPA in strategy.stages
    assert strategy.estimated_cost == 1.30
    assert strategy.estimated_time_min == 90


def test_constraint_with_heavy_auto_level():
    """Test constraints work with heavy auto_level."""
    # COMPLEX heavy normally costs $1.95, limit is $1.00
    strategy = select_optimization_strategy(
        "COMPLEX", "heavy", budget_limit=1.00, time_limit_min=None
    )

    # Should downgrade to 2-stage or 1-stage
    assert len(strategy.stages) <= 2
    assert strategy.estimated_cost < 1.00
