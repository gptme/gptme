"""Complexity scoring algorithm for task analysis.

Implements weighted scoring system:
- Scope: 40% (files, lines impacted)
- Dependencies: 30% (imports, classes, hierarchy)
- Patterns: 20% (keywords, design mentions)
- Context: 10% (reference availability)

Returns score 0.0-1.0:
- 0.0-0.3: Focused (simple fixes, single-file changes)
- 0.3-0.7: Mixed (refactoring, multi-file changes)
- 0.7-1.0: Architecture (implementations, designs, systems)
"""

from .indicators import TaskIndicators


def calculate_complexity_score(indicators: TaskIndicators) -> float:
    """Calculate task complexity score from indicators.

    Args:
        indicators: Complete set of task indicators

    Returns:
        Complexity score 0.0-1.0 where:
        - 0.0-0.3: Focused task (aggressive compression)
        - 0.3-0.7: Mixed task (moderate compression)
        - 0.7-1.0: Architecture task (conservative compression)
    """
    score = 0.0

    # Scope indicators (40% weight)
    score += _score_scope(indicators.scope)

    # Dependency indicators (30% weight)
    score += _score_dependencies(indicators.dependencies)

    # Pattern indicators (20% weight)
    score += _score_patterns(indicators.patterns)

    # Context indicators (10% weight)
    score += _score_context(indicators.context)

    return min(score, 1.0)


def _score_scope(scope) -> float:
    """Score scope indicators (40% total weight).

    Thresholds:
    - Focused: 1-2 files, <100 lines
    - Mixed: 2-5 files, 100-500 lines
    - Architecture: 5+ files, 500+ lines
    """
    score = 0.0

    # Files count (25% of total, 62.5% of scope)
    if scope.files_count > 5:
        score += 0.25
    elif scope.files_count > 2:
        score += 0.15

    # Lines estimate (15% of total, 37.5% of scope)
    if scope.lines_estimate > 500:
        score += 0.15
    elif scope.lines_estimate > 100:
        score += 0.10

    # New files indicator (bonus for creating vs editing)
    if scope.new_files and scope.files_count > 3:
        score += 0.05

    return score


def _score_dependencies(deps) -> float:
    """Score dependency indicators (30% total weight).

    Architecture signals:
    - Multiple new classes
    - Many external libraries
    - Deep inheritance hierarchies
    """
    score = 0.0

    # New classes (15% of total)
    if deps.new_classes > 2:
        score += 0.15
    elif deps.new_classes > 0:
        score += 0.08

    # External libraries (10% of total)
    if len(deps.external_libs) > 3:
        score += 0.10
    elif len(deps.external_libs) > 1:
        score += 0.05

    # Inheritance depth (5% of total)
    if deps.inheritance_depth > 2:
        score += 0.05
    elif deps.inheritance_depth > 1:
        score += 0.03

    return score


def _score_patterns(patterns) -> float:
    """Score pattern indicators (20% total weight).

    Architecture keywords:
    - "implement", "design", "create", "build"
    - Design/architecture mentions
    - Research/analysis indicators (Priority 1)
    - Design/planning indicators (Priority 2)
    """
    score = 0.0

    # Design mentions (10% of total)
    if patterns.mentions_design:
        score += 0.10

    # Architecture verbs (10% of total)
    architecture_verbs = {"implement", "design", "create", "build", "architect"}
    if any(verb in patterns.verbs for verb in architecture_verbs):
        score += 0.10

    # Reference mentions (bonus, helps identify need for examples)
    if patterns.mentions_reference:
        score += 0.05

    # Priority 1: Research/analysis indicators (boost to mixed range)
    research_keywords = {
        "research",
        "analyze",
        "investigation",
        "analysis",
        "comparative",
        "evaluate",
    }
    if any(kw in patterns.keywords for kw in research_keywords):
        score += 0.15  # Strong boost toward mixed complexity

    # Priority 2: Design/planning indicators (boost to architecture range)
    planning_keywords = {"planning", "roadmap", "phases", "milestones"}
    if any(kw in patterns.keywords for kw in planning_keywords):
        score += 0.20  # Strong boost toward architecture complexity

    return score


def _score_context(context) -> float:
    """Score context indicators (10% total weight).

    Missing context increases complexity:
    - No reference implementations
    - No examples
    - No tests/docs
    """
    score = 0.0

    # Missing reference implementations (5% of total)
    if not context.reference_impls:
        score += 0.05

    # Missing examples (3% of total)
    if not context.examples_available:
        score += 0.03

    # Missing tests/docs (2% of total)
    if not context.tests_exist and not context.docs_exist:
        score += 0.02

    return score


def classify_complexity(
    score: float,
    thresholds: dict[str, float] | None = None,
) -> str:
    """Classify complexity score into category.

    Args:
        score: Complexity score 0.0-1.0
        thresholds: Custom thresholds (defaults to {"focused": 0.3, "architecture": 0.7})

    Returns:
        Category: "focused", "mixed", or "architecture"
    """
    if thresholds is None:
        thresholds = {"focused": 0.3, "architecture": 0.7}

    if score < thresholds["focused"]:
        return "focused"
    elif score < thresholds["architecture"]:
        return "mixed"
    else:
        return "architecture"
