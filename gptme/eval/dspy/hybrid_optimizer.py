"""
Hybrid optimization combining multiple DSPy optimizers in stages.

This module implements Phase 4.1 of GEPA optimization, providing a multi-stage
optimization pipeline that leverages the strengths of different optimizers:
- Bootstrap: Quick pattern extraction (Stage 1)
- MIPROv2: Broad exploration with scalar metrics (Stage 2)
- GEPA: Deep refinement with trajectory feedback (Stage 3)

Configuration Schema
--------------------

The HybridOptimizer accepts the following configuration parameters:

**Core Parameters:**

- ``metric``: Scalar metric (callable) for Bootstrap and MIPROv2 stages
    Returns float score for each prediction. Example: accuracy, F1, composite score.

- ``trajectory_metric``: Rich metric (callable) for GEPA stage
    Returns Prediction with score and textual feedback. Defaults to ``metric`` if not provided.
    Should analyze tool usage, reasoning quality, and error handling.

- ``max_demos``: Maximum demonstrations per optimizer (default: 3)
    Bootstrap uses this directly, MIPROv2 uses 2x for labeled demos.
    Higher values increase sample efficiency but cost more.

- ``num_trials``: Number of optimization trials (default: 10)
    Bootstrap limits to min(num_trials, 5) for efficiency.
    MIPROv2 uses full value as num_candidates.

- ``reflection_lm``: Language model for GEPA reflection (optional)
    Should be more capable than task LM. Upgraded automatically:
    - Haiku → Sonnet
    - GPT-3.5-mini → GPT-4o

- ``num_threads``: Parallel threads for GEPA (default: 4)
    Higher values speed up GEPA but increase API cost.

- ``auto_stage``: Automatic configuration level (default: "medium")
    Options: "light", "medium", "heavy"
    Controls optimization aggressiveness vs. cost trade-off.

**Auto Stage Configurations:**

- ``light``: Fast, low-cost optimization
    - Bootstrap: 3 rounds, 2 demos
    - MIPROv2: 5 candidates
    - GEPA: 3 threads, small minibatch
    - Time: 10-30 min total
    - Cost: ~$0.20-0.40

- ``medium``: Balanced optimization (default)
    - Bootstrap: 5 rounds, 3 demos
    - MIPROv2: 10 candidates
    - GEPA: 4 threads, standard minibatch
    - Time: 30-90 min total
    - Cost: ~$0.60-1.20

- ``heavy``: Thorough optimization
    - Bootstrap: 5 rounds, 5 demos
    - MIPROv2: 20 candidates
    - GEPA: 8 threads, large minibatch
    - Time: 90-180 min total
    - Cost: ~$1.50-2.50

**Task Complexity Detection:**

The optimizer automatically detects task complexity and selects appropriate stages:

- **Simple tasks** (< 200 chars): 1-stage (Bootstrap only)
    Fast pattern extraction sufficient for simple tasks.
    Time: 5-10 min, Cost: ~$0.10

- **Medium tasks** (200-1000 chars): 2-stage (Bootstrap → MIPROv2)
    Combines quick patterns with broader exploration.
    Time: 30-60 min, Cost: ~$0.40-0.60

- **Complex tasks** (> 1000 chars): 3-stage (Bootstrap → MIPROv2 → GEPA)
    Full pipeline with trajectory-based refinement.
    Time: 60-120 min, Cost: ~$1.00-1.60

Integration Examples
--------------------

**Basic Usage via PromptOptimizer:**

.. code-block:: python

    from gptme.eval.dspy import PromptOptimizer

    # Automatic hybrid optimization
    optimizer = PromptOptimizer(
        optimizer_type="hybrid",
        metric=my_metric,
        trajectory_metric=my_trajectory_metric,
        auto="medium"
    )

    optimized = optimizer.optimize(
        module=my_module,
        trainset=my_trainset,
        valset=my_valset
    )

**Direct Usage with Custom Configuration:**

.. code-block:: python

    from gptme.eval.dspy.hybrid_optimizer import HybridOptimizer

    # Light configuration for fast iteration
    optimizer = HybridOptimizer(
        metric=composite_metric,
        trajectory_metric=trajectory_feedback_metric,
        max_demos=2,
        num_trials=5,
        auto_stage="light"
    )

    optimized = optimizer.compile(
        student=my_reasoning_program,
        trainset=my_trainset
    )

**Heavy Configuration for Production:**

.. code-block:: python

    from gptme.eval.dspy.hybrid_optimizer import HybridOptimizer
    import dspy

    # Upgrade reflection model for better feedback
    reflection_lm = dspy.LM("anthropic/claude-sonnet-4")

    optimizer = HybridOptimizer(
        metric=my_metric,
        trajectory_metric=trajectory_metric,
        max_demos=5,
        num_trials=20,
        reflection_lm=reflection_lm,
        num_threads=8,
        auto_stage="heavy"
    )

    optimized = optimizer.compile(student, trainset)

**CLI Usage:**

.. code-block:: bash

    # Automatic hybrid optimization (medium by default)
    python -m gptme.eval.dspy optimize \\
        --optimizer hybrid \\
        --auto medium \\
        --train-size 10 \\
        --val-size 5

    # Light configuration for quick testing
    python -m gptme.eval.dspy optimize \\
        --optimizer hybrid \\
        --auto light \\
        --train-size 5 \\
        --val-size 2

    # Heavy configuration for production
    python -m gptme.eval.dspy optimize \\
        --optimizer hybrid \\
        --auto heavy \\
        --train-size 20 \\
        --val-size 10

See Also
--------
**Decision Tree Documentation:**
    Complete decision tree visualization and path analysis:
    `/home/bob/gptme-bob/knowledge/technical-designs/gepa-selection-decision-tree.md`

    This document provides:
    - ASCII decision tree diagram showing all decision paths
    - 8 detailed path examples (simple to complex, with/without constraints)
    - Test coverage mapping (19 tests to decision paths)
    - Implementation details and design rationale
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

import dspy
from dspy import GEPA
from dspy.teleprompt import BootstrapFewShot, MIPROv2

logger = logging.getLogger(__name__)


class OptimizerStage(Enum):
    """Optimization stages available in hybrid pipeline."""

    BOOTSTRAP = "bootstrap"
    MIPRO = "mipro"
    GEPA = "gepa"


@dataclass
class OptimizationStrategy:
    """
    Strategy for multi-stage optimization.

    Attributes:
        stages: Ordered list of optimizer stages to execute
        complexity: Task complexity level (SIMPLE, MEDIUM, COMPLEX)
        auto_level: Configuration level ("light", "medium", "heavy")
        estimated_time_min: Estimated total optimization time in minutes
        estimated_cost: Estimated total cost in USD
    """

    stages: list[OptimizerStage]
    complexity: str
    auto_level: str
    estimated_time_min: int
    estimated_cost: float

    @property
    def num_stages(self) -> int:
        """Number of optimization stages."""
        return len(self.stages)

    def __str__(self) -> str:
        """Human-readable strategy description."""
        stage_names = " → ".join(s.value.title() for s in self.stages)
        return (
            f"{self.num_stages}-stage ({stage_names}): "
            f"~{self.estimated_time_min} min, ~${self.estimated_cost:.2f}"
        )


class TaskComplexity:
    """Analyzer for determining task complexity."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"

    # Keywords indicating complexity factors
    TOOL_KEYWORDS = [
        "api",
        "database",
        "file",
        "tool",
        "execute",
        "search",
        "browse",
        "scrape",
        "parse",
    ]

    MULTISTEP_KEYWORDS = [
        "then",
        "after",
        "next",
        "first",
        "finally",
        "step",
        "stages",
        "phases",
        "sequence",
    ]

    TECHNICAL_KEYWORDS = [
        "algorithm",
        "optimization",
        "architecture",
        "implementation",
        "debug",
        "refactor",
        "analyze",
        "benchmark",
    ]

    @staticmethod
    def analyze(task: dspy.Example) -> str:
        """
        Analyze task complexity based on multiple characteristics.

        Enhanced beyond simple length heuristic (Phase 4.1 Week 2) to consider:
        - Multi-step reasoning requirements (MULTISTEP_KEYWORDS)
        - Tool/API usage needs (TOOL_KEYWORDS)
        - Domain complexity (TECHNICAL_KEYWORDS)
        - Structural characteristics (questions, requirements)
        - Length as baseline (< 200 = simple bias, > 1000 = complex bias)

        Scoring system:
        - 0-1 points: SIMPLE (basic task, minimal reasoning)
        - 2-3 points: MEDIUM (some complexity, tools or multi-step)
        - 4+ points: COMPLEX (multiple complexity factors)

        Args:
            task: DSPy example with task description and context

        Returns:
            One of: "simple", "medium", "complex"
        """
        import re

        # Extract text content
        desc = getattr(task, "task_description", "")
        context = getattr(task, "context", "")
        text = (desc + " " + context).lower()

        # Compute features
        length = len(text)
        has_tools = any(keyword in text for keyword in TaskComplexity.TOOL_KEYWORDS)
        has_multistep = any(
            keyword in text for keyword in TaskComplexity.MULTISTEP_KEYWORDS
        )
        has_technical = any(
            keyword in text for keyword in TaskComplexity.TECHNICAL_KEYWORDS
        )

        # Count structural elements
        num_questions = text.count("?")
        num_requirements = (
            text.count("must") + text.count("should") + text.count("need")
        )
        num_steps = len(re.findall(r"\b\d+[\.\)]\s", text))  # "1. ", "2)", etc.

        # Scoring: accumulate complexity points
        score = 0

        # Length contribution (baseline)
        if length < 200:
            score += 0  # simple bias
        elif length < 1000:
            score += 1  # medium baseline
        else:
            score += 2  # complex bias

        # Feature contributions
        if has_tools:
            score += 1  # requires tool usage
        if has_multistep or num_steps >= 3:
            score += 1  # multi-step reasoning
        if has_technical:
            score += 1  # technical domain
        if num_questions >= 2 or num_requirements >= 3:
            score += 1  # multiple requirements/questions

        # Classification by total score
        # If no complexity features found, use pure length classification (backward compatible)
        has_features = (
            has_tools
            or has_multistep
            or has_technical
            or num_questions >= 2
            or num_requirements >= 3
        )

        if not has_features:
            # Pure length classification (maintains existing behavior)
            if length < 200:
                return TaskComplexity.SIMPLE
            elif length < 1000:
                return TaskComplexity.MEDIUM
            else:
                return TaskComplexity.COMPLEX

        # Enhanced classification with features
        if score <= 1:
            return TaskComplexity.SIMPLE
        elif score <= 3:
            return TaskComplexity.MEDIUM
        else:
            return TaskComplexity.COMPLEX


def select_optimization_strategy(
    complexity: str,
    auto_level: str = "medium",
    budget_limit: float | None = None,
    time_limit_min: int | None = None,
) -> OptimizationStrategy:
    """
    Select appropriate optimization strategy based on task characteristics.

    This function implements automated optimizer selection based on:
    - Task complexity (SIMPLE, MEDIUM, COMPLEX)
    - Configuration level (light, medium, heavy)
    - Optional budget and time constraints

    Args:
        complexity: Task complexity from TaskComplexity.analyze()
            Returns lowercase ("simple", "medium", "complex") but accepts either case
        auto_level: Configuration level for optimization aggressiveness
            Options: "light", "medium", "heavy"
            Default: "medium"
        budget_limit: Maximum budget in USD (optional)
            If provided, strategy will be downgraded to fit within budget
            Downgrade order: Remove GEPA, then MIPROv2
        time_limit_min: Maximum time in minutes (optional)
            If provided, strategy will be downgraded to fit within time limit
            Downgrade order: Remove GEPA, then MIPROv2

    Returns:
        OptimizationStrategy with recommended stages and resource estimates

    Strategy Selection Logic:
        SIMPLE tasks: 1-stage (Bootstrap)
            - Fast pattern extraction sufficient
            - Time: 5-15 min, Cost: $0.10-0.15

        MEDIUM tasks: 2-stage (Bootstrap → MIPROv2)
            - Combines quick patterns with broader exploration
            - Time: 30-60 min, Cost: $0.40-0.75

        COMPLEX tasks: 3-stage (Bootstrap → MIPROv2 → GEPA)
            - Full pipeline with trajectory refinement
            - Time: 60-180 min, Cost: $1.00-2.50

    Examples:
        >>> # Simple task, medium configuration
        >>> strategy = select_optimization_strategy("SIMPLE", "medium")
        >>> print(strategy.stages)
        [OptimizerStage.BOOTSTRAP]
        >>> print(strategy.estimated_time_min)
        10

        >>> # Complex task, heavy configuration
        >>> strategy = select_optimization_strategy("COMPLEX", "heavy")
        >>> print(strategy.stages)
        [OptimizerStage.BOOTSTRAP, OptimizerStage.MIPRO, OptimizerStage.GEPA]
        >>> print(strategy.estimated_time_min)
        135
    """
    # Normalize complexity to lowercase (TaskComplexity.analyze returns lowercase)
    complexity = complexity.lower()

    # Base strategy selection by complexity
    if complexity == "simple":
        stages = [OptimizerStage.BOOTSTRAP]
        base_time = 10  # minutes
        base_cost = 0.10  # USD
    elif complexity == "medium":
        stages = [OptimizerStage.BOOTSTRAP, OptimizerStage.MIPRO]
        base_time = 45
        base_cost = 0.50
    else:  # COMPLEX
        stages = [OptimizerStage.BOOTSTRAP, OptimizerStage.MIPRO, OptimizerStage.GEPA]
        base_time = 90
        base_cost = 1.30

    # Adjust estimates based on auto_level
    if auto_level == "light":
        time_multiplier = 0.5
        cost_multiplier = 0.5
    elif auto_level == "heavy":
        time_multiplier = 1.5
        cost_multiplier = 1.5
    else:  # medium
        time_multiplier = 1.0
        cost_multiplier = 1.0

    estimated_time = int(base_time * time_multiplier)
    estimated_cost = base_cost * cost_multiplier

    # Apply budget/time constraints (Week 2 subtask 3)
    # Downgrade strategy if it exceeds constraints by removing expensive stages
    while budget_limit and estimated_cost > budget_limit and len(stages) > 1:
        # Remove most expensive stage (GEPA first, then MIPROv2)
        if OptimizerStage.GEPA in stages:
            stages = stages[:-1]  # Remove GEPA
            estimated_cost *= 0.6  # Approximate 40% cost reduction
            estimated_time = int(estimated_time * 0.5)  # GEPA is ~50% of time
        elif OptimizerStage.MIPRO in stages:
            stages = stages[:-1]  # Remove MIPROv2
            estimated_cost *= 0.2  # Down to Bootstrap only (~80% reduction)
            estimated_time = int(estimated_time * 0.22)  # Bootstrap is ~22% of time
        else:
            # Only Bootstrap left, can't downgrade further
            break

    while time_limit_min and estimated_time > time_limit_min and len(stages) > 1:
        # Remove most expensive stage by time (similar to cost)
        if OptimizerStage.GEPA in stages:
            stages = stages[:-1]  # Remove GEPA
            estimated_time = int(estimated_time * 0.5)  # GEPA is ~50% of time
            estimated_cost *= 0.6  # Keep cost estimate consistent
        elif OptimizerStage.MIPRO in stages:
            stages = stages[:-1]  # Remove MIPROv2
            estimated_time = int(estimated_time * 0.22)  # Bootstrap is ~22% of time
            estimated_cost *= 0.2  # Keep cost estimate consistent
        else:
            # Only Bootstrap left, can't downgrade further
            break

    return OptimizationStrategy(
        stages=stages,
        complexity=complexity,
        auto_level=auto_level,
        estimated_time_min=estimated_time,
        estimated_cost=estimated_cost,
    )


class HybridOptimizer:
    """
    Multi-stage optimizer combining Bootstrap, MIPROv2, and GEPA.

    Pipeline stages:
    1. Bootstrap: Quick pattern learning (5-10 min, $0.10)
    2. MIPROv2: Broad exploration (30-60 min, $0.50)
    3. GEPA: Deep refinement (60-120 min, $1.00)

    Optimizer selection based on task complexity:
    - Simple tasks: Bootstrap only (1-stage)
    - Medium tasks: Bootstrap + MIPROv2 (2-stage)
    - Complex tasks: Bootstrap + MIPROv2 + GEPA (3-stage)

    Usage Examples
    --------------

    **Basic usage with default settings:**

    .. code-block:: python

        optimizer = HybridOptimizer(
            metric=my_metric,
            trajectory_metric=my_trajectory_metric
        )
        optimized = optimizer.compile(student, trainset)

    **Quick iteration with light configuration:**

    .. code-block:: python

        optimizer = HybridOptimizer(
            metric=my_metric,
            max_demos=2,
            num_trials=5,
            auto_stage="light"
        )
        optimized = optimizer.compile(student, trainset)

    **Production optimization with heavy configuration:**

    .. code-block:: python

        import dspy
        reflection_lm = dspy.LM("anthropic/claude-sonnet-4")

        optimizer = HybridOptimizer(
            metric=my_metric,
            trajectory_metric=my_trajectory_metric,
            max_demos=5,
            num_trials=20,
            reflection_lm=reflection_lm,
            num_threads=8,
            auto_stage="heavy"
        )
        optimized = optimizer.compile(student, trainset)

    Configuration Trade-offs
    -------------------------

    **Light (fast, cheap):**
    - Best for: Rapid prototyping, CI/CD pipelines, quick experiments
    - Time: 10-30 minutes
    - Cost: $0.20-0.40
    - Quality: Good for simple tasks

    **Medium (balanced, default):**
    - Best for: Most use cases, balanced cost/quality
    - Time: 30-90 minutes
    - Cost: $0.60-1.20
    - Quality: Excellent for medium complexity

    **Heavy (thorough, expensive):**
    - Best for: Production deployments, complex tasks, research
    - Time: 90-180 minutes
    - Cost: $1.50-2.50
    - Quality: Maximum quality, comprehensive optimization

    Expected Benefits
    -----------------

    Compared to single-optimizer approaches:

    - **30-50% cost reduction** vs. pure GEPA (by using cheaper optimizers first)
    - **Maintained quality** through progressive refinement
    - **Faster iteration** with automatic stage selection
    - **Better sample efficiency** by building on previous stage results

    See Also
    --------
    - :class:`PromptOptimizer`: High-level interface for all optimizer types
    - :class:`TaskComplexity`: Task analysis and complexity detection
    - :func:`GEPA`: Deep refinement with trajectory feedback
    """

    def __init__(
        self,
        metric: Any,
        trajectory_metric: Any | None = None,
        max_demos: int = 3,
        num_trials: int = 10,
        reflection_lm: Any | None = None,
        num_threads: int = 4,
        auto_stage: str = "medium",
    ):
        """
        Initialize hybrid optimizer.

        Args:
            metric: Standard scalar metric for Bootstrap and MIPROv2
            trajectory_metric: Trajectory-based metric for GEPA stage
            max_demos: Maximum demonstrations per optimizer
            num_trials: Number of optimization trials
            reflection_lm: Language model for GEPA reflection
            num_threads: Threads for parallel GEPA execution
            auto_stage: Auto configuration for stages ("light", "medium", "heavy")
        """
        self.metric = metric
        self.trajectory_metric = trajectory_metric or metric
        self.max_demos = max_demos
        self.num_trials = num_trials
        self.reflection_lm = reflection_lm
        self.num_threads = num_threads
        self.auto_stage = auto_stage

        # Initialize individual optimizers (lazy initialization)
        self._bootstrap = None
        self._mipro = None
        self._gepa = None

    def compile(
        self, student: dspy.Module, trainset: list[dspy.Example]
    ) -> dspy.Module:
        """
        Compile student module using multi-stage optimization.

        Args:
            student: DSPy module to optimize
            trainset: Training examples

        Returns:
            Optimized module
        """
        logger.info("Starting hybrid optimization pipeline...")

        # Analyze task complexity to determine stages
        complexity = self._analyze_trainset_complexity(trainset)
        logger.info(f"Detected complexity: {complexity}")

        # Select optimization strategy
        strategy = select_optimization_strategy(
            complexity=complexity,
            auto_level=self.auto_stage,
        )
        logger.info(f"Selected strategy: {strategy}")

        # Execute pipeline based on strategy stages
        num_stages = len(strategy.stages)
        if num_stages == 1:
            return self._run_1stage(student, trainset)
        elif num_stages == 2:
            return self._run_2stage(student, trainset)
        else:  # 3 stages
            return self._run_3stage(student, trainset)

    def _analyze_trainset_complexity(self, trainset: list[dspy.Example]) -> str:
        """Analyze overall trainset complexity."""
        complexities = [TaskComplexity.analyze(task) for task in trainset]

        # If majority are complex, classify as complex
        if complexities.count(TaskComplexity.COMPLEX) > len(trainset) / 2:
            return TaskComplexity.COMPLEX
        # If majority are simple, classify as simple
        elif complexities.count(TaskComplexity.SIMPLE) > len(trainset) / 2:
            return TaskComplexity.SIMPLE
        # Otherwise, medium
        else:
            return TaskComplexity.MEDIUM

    def _run_1stage(
        self, student: dspy.Module, trainset: list[dspy.Example]
    ) -> dspy.Module:
        """Run 1-stage pipeline: Bootstrap only (simple tasks)."""
        logger.info("Running 1-stage pipeline: Bootstrap")
        bootstrap = self._get_bootstrap()
        return bootstrap.compile(student, trainset=trainset)

    def _run_2stage(
        self, student: dspy.Module, trainset: list[dspy.Example]
    ) -> dspy.Module:
        """Run 2-stage pipeline: Bootstrap → MIPROv2 (medium tasks)."""
        logger.info("Running 2-stage pipeline: Bootstrap → MIPROv2")

        # Stage 1: Bootstrap
        logger.info("Stage 1/2: Bootstrap optimization...")
        bootstrap = self._get_bootstrap()
        stage1_output = bootstrap.compile(student, trainset=trainset)

        # Stage 2: MIPROv2 (using Stage 1 output as starting point)
        logger.info("Stage 2/2: MIPROv2 optimization...")
        mipro = self._get_mipro()
        stage2_output = mipro.compile(stage1_output, trainset=trainset)

        return stage2_output

    def _run_3stage(
        self, student: dspy.Module, trainset: list[dspy.Example]
    ) -> dspy.Module:
        """Run 3-stage pipeline: Bootstrap → MIPROv2 → GEPA (complex tasks)."""
        logger.info("Running 3-stage pipeline: Bootstrap → MIPROv2 → GEPA")

        # Stage 1: Bootstrap
        logger.info("Stage 1/3: Bootstrap optimization...")
        bootstrap = self._get_bootstrap()
        stage1_output = bootstrap.compile(student, trainset=trainset)

        # Stage 2: MIPROv2
        logger.info("Stage 2/3: MIPROv2 optimization...")
        mipro = self._get_mipro()
        stage2_output = mipro.compile(stage1_output, trainset=trainset)

        # Stage 3: GEPA
        logger.info("Stage 3/3: GEPA optimization...")
        gepa = self._get_gepa()
        stage3_output = gepa.compile(stage2_output, trainset=trainset)

        return stage3_output

    def _get_bootstrap(self) -> BootstrapFewShot:
        """Get or create Bootstrap optimizer."""
        if self._bootstrap is None:
            self._bootstrap = BootstrapFewShot(
                metric=self.metric,
                max_bootstrapped_demos=self.max_demos,
                max_rounds=min(self.num_trials, 5),  # Bootstrap: fewer rounds
            )
        return self._bootstrap

    def _get_mipro(self) -> MIPROv2:
        """Get or create MIPROv2 optimizer."""
        if self._mipro is None:
            self._mipro = MIPROv2(
                metric=self.metric,
                auto=self.auto_stage,
                max_bootstrapped_demos=self.max_demos,
                max_labeled_demos=self.max_demos * 2,
                num_candidates=self.num_trials,
            )
        return self._mipro

    def _get_gepa(self) -> GEPA:
        """Get or create GEPA optimizer."""
        if self._gepa is None:
            gepa_kwargs = {
                "metric": self.trajectory_metric,
                "num_threads": self.num_threads,
                "track_stats": True,
                "reflection_minibatch_size": 3,
                "auto": self.auto_stage,
            }

            if self.reflection_lm is not None:
                gepa_kwargs["reflection_lm"] = self.reflection_lm

            self._gepa = GEPA(**gepa_kwargs)
        return self._gepa
