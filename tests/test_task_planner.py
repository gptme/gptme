"""Tests for MIQ-based task planner."""

from pathlib import Path
from textwrap import dedent

import pytest

from gptme.tasks.loader import Task
from gptme.tasks.planner import MIQPlanner, MIQScore


@pytest.fixture
def strategic_task(tmp_path: Path) -> Task:
    """Create a strategic task (dev + automation)."""
    task_file = tmp_path / "strategic-task.md"
    task_file.write_text(
        dedent("""\
        ---
        state: active
        priority: high
        tags: [dev, automation, '@autonomous']
        depends: []
        ---
        # Strategic Task
        High-value self-improvement task.
        """)
    )
    return Task.from_file(task_file)


@pytest.fixture
def project_task(tmp_path: Path) -> Task:
    """Create a project task (gptme)."""
    task_file = tmp_path / "project-task.md"
    task_file.write_text(
        dedent("""\
        ---
        state: new
        priority: medium
        tags: [gptme, feature]
        depends: [other-task]
        ---
        # Project Task
        Aiding gptme project.
        """)
    )
    return Task.from_file(task_file)


@pytest.fixture
def social_task(tmp_path: Path) -> Task:
    """Create a social task (community)."""
    task_file = tmp_path / "social-task.md"
    task_file.write_text(
        dedent("""\
        ---
        state: new
        priority: low
        tags: [community, engagement]
        depends: []
        ---
        # Social Task
        Building relationships.
        """)
    )
    return Task.from_file(task_file)


def test_miq_score_strategic_task(strategic_task: Task):
    """Test MIQ scoring for strategic task."""
    score = MIQScore.calculate(strategic_task)

    # Strategic task should score highly
    assert (
        score.total >= 0.8
    ), f"Strategic task should have high total score, got {score.total}"
    assert score.goal_alignment == 1.0, "Dev + automation = perfect alignment"
    assert score.capability_match == 1.0, "@autonomous tag = perfect match"
    assert score.urgency >= 0.9, "Active + high priority = high urgency"


def test_miq_score_project_task(project_task: Task):
    """Test MIQ scoring for project task."""
    score = MIQScore.calculate(project_task)

    # Project task should score well
    assert (
        0.6 <= score.total <= 0.9
    ), f"Project task should score well, got {score.total}"
    assert score.goal_alignment == 0.9, "gptme tag = project alignment"
    assert score.impact_potential >= 0.7, "Dependencies = high impact"


def test_miq_score_social_task(social_task: Task):
    """Test MIQ scoring for social task."""
    score = MIQScore.calculate(social_task)

    # Social task should score moderately
    assert (
        0.5 <= score.total <= 0.8
    ), f"Social task should score moderately, got {score.total}"
    assert score.goal_alignment == 0.7, "Community tag = social alignment"
    assert score.urgency < 0.8, "New + low priority = lower urgency"


def test_planner_calculate_miq_score(strategic_task: Task):
    """Test MIQPlanner.calculate_miq_score() wrapper."""
    score = MIQPlanner.calculate_miq_score(strategic_task)

    assert isinstance(score, float), "Should return float"
    assert 0.0 <= score <= 1.0, "Score should be in [0.0, 1.0]"
    assert score >= 0.8, f"Strategic task should score highly, got {score}"


def test_planner_score_tasks(
    strategic_task: Task, project_task: Task, social_task: Task
):
    """Test scoring and sorting multiple tasks."""
    tasks = [social_task, project_task, strategic_task]  # Unsorted order

    scored = MIQPlanner.score_tasks(tasks)

    # Check structure
    assert len(scored) == 3, "Should score all tasks"
    assert all(isinstance(t, Task) for t, _ in scored), "Should return Task objects"
    assert all(
        isinstance(s, MIQScore) for _, s in scored
    ), "Should return MIQScore objects"

    # Check sorting (descending by total score)
    scores = [s.total for _, s in scored]
    assert scores == sorted(
        scores, reverse=True
    ), "Should be sorted by total score descending"

    # Check expected order (strategic should score highest)
    assert (
        scored[0][0].id == strategic_task.id
    ), f"Strategic task should be first, got {scored[0][0].id}"


def test_planner_explain_score(strategic_task: Task):
    """Test score explanation generation."""
    explanation = MIQPlanner.explain_score(strategic_task)

    # Check explanation contains expected elements
    assert "MIQ Score" in explanation, "Should have title"
    assert "Goal Alignment" in explanation, "Should show goal alignment"
    assert "Impact Potential" in explanation, "Should show impact potential"
    assert "Urgency" in explanation, "Should show urgency"
    assert "Capability Match" in explanation, "Should show capability match"
    assert "Dependency Value" in explanation, "Should show dependency value"
    assert "active" in explanation, "Should show state"
    assert "high" in explanation, "Should show priority"
    assert "dev" in explanation or "automation" in explanation, "Should show tags"


def test_score_weights_sum_to_one():
    """Test that scoring weights sum to 1.0."""
    weights = {
        "goal_alignment": 0.30,
        "impact_potential": 0.25,
        "urgency": 0.20,
        "capability_match": 0.15,
        "dependency_value": 0.10,
    }

    total_weight = sum(weights.values())
    assert abs(total_weight - 1.0) < 0.001, "Weights should sum to 1.0"


def test_score_range_validation(tmp_path: Path):
    """Test that all scores stay within [0.0, 1.0] range."""
    # Create task with extreme values
    task_file = tmp_path / "extreme-task.md"
    task_file.write_text(
        dedent("""\
        ---
        state: active
        priority: high
        tags: [dev, automation, infrastructure, '@autonomous', gptme]
        depends: [task1, task2, task3]
        ---
        # Extreme Task
        All high-scoring attributes.
        """)
    )
    task = Task.from_file(task_file)

    score = MIQScore.calculate(task)

    # All components should be in valid range
    assert 0.0 <= score.goal_alignment <= 1.0, "Goal alignment in range"
    assert 0.0 <= score.impact_potential <= 1.0, "Impact potential in range"
    assert 0.0 <= score.urgency <= 1.0, "Urgency in range"
    assert 0.0 <= score.capability_match <= 1.0, "Capability match in range"
    assert 0.0 <= score.dependency_value <= 1.0, "Dependency value in range"
    assert 0.0 <= score.total <= 1.0, "Total score in range"
