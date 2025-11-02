"""MIQ-based task planning and scoring for task loop mode.

The MIQ (Most Important Question) planner evaluates tasks based on strategic
importance, helping prioritize work that creates the most long-term value.
"""

from dataclasses import dataclass

from .loader import Task


@dataclass
class MIQScore:
    """MIQ scoring breakdown for a task."""

    goal_alignment: float  # 0.0-1.0: How well aligned with strategic goals
    impact_potential: float  # 0.0-1.0: How much progress this enables
    urgency: float  # 0.0-1.0: Time sensitivity
    capability_match: float  # 0.0-1.0: Current capability vs task complexity
    dependency_value: float  # 0.0-1.0: Unblocks other work
    total: float  # Weighted average

    @classmethod
    def calculate(cls, task: Task) -> "MIQScore":
        """Calculate MIQ score for a task.

        Args:
            task: Task to score

        Returns:
            MIQScore with breakdown
        """
        # Goal alignment: Check tags for strategic keywords
        goal_alignment = cls._calculate_goal_alignment(task)

        # Impact potential: High priority + many dependencies = high impact
        impact_potential = cls._calculate_impact_potential(task)

        # Urgency: Based on priority and state
        urgency = cls._calculate_urgency(task)

        # Capability match: Autonomous-tagged tasks match current capabilities
        capability_match = cls._calculate_capability_match(task)

        # Dependency value: Tasks with dependents unblock more work
        dependency_value = cls._calculate_dependency_value(task)

        # Weighted average (goal alignment and impact weighted higher)
        weights = {
            "goal_alignment": 0.30,
            "impact_potential": 0.25,
            "urgency": 0.20,
            "capability_match": 0.15,
            "dependency_value": 0.10,
        }

        total = (
            goal_alignment * weights["goal_alignment"]
            + impact_potential * weights["impact_potential"]
            + urgency * weights["urgency"]
            + capability_match * weights["capability_match"]
            + dependency_value * weights["dependency_value"]
        )

        return cls(
            goal_alignment=goal_alignment,
            impact_potential=impact_potential,
            urgency=urgency,
            capability_match=capability_match,
            dependency_value=dependency_value,
            total=total,
        )

    @staticmethod
    def _calculate_goal_alignment(task: Task) -> float:
        """Calculate goal alignment score.

        Strategic goals from ABOUT.md:
        - Self-improvement (capabilities, quality, knowledge)
        - Aiding projects (gptme, ActivityWatch, Erik's work)
        - Making friends (relationships, collaboration)
        - Getting attention (reputation, projects)
        - Finding opportunities (trends, needs, gaps)
        - Self-preservation (operational stability)
        """
        strategic_tags = {
            "dev",
            "automation",
            "infrastructure",
            "learning",
            "testing",
            "ai",
        }
        project_tags = {"gptme", "activitywatch", "website", "tasks"}
        social_tags = {"community", "engagement", "collaboration"}

        tags = set(task.tags)

        # Strategic alignment
        if tags & strategic_tags:
            return 1.0  # Self-improvement

        # Project alignment
        if tags & project_tags:
            return 0.9  # Aiding projects

        # Social alignment
        if tags & social_tags:
            return 0.7  # Making friends

        return 0.5  # Neutral

    @staticmethod
    def _calculate_impact_potential(task: Task) -> float:
        """Calculate impact potential score.

        High impact = enables other work, has dependencies.
        """
        score = 0.5  # Base score

        # High priority = high impact
        if task.priority == "high":
            score += 0.3
        elif task.priority == "medium":
            score += 0.1

        # Tasks with dependencies have higher impact (unblock others)
        if task.depends:
            score += 0.2

        return min(score, 1.0)

    @staticmethod
    def _calculate_urgency(task: Task) -> float:
        """Calculate urgency score.

        Active tasks are more urgent than new.
        High priority is more urgent.
        """
        score = 0.5  # Base score

        # Active tasks are urgent
        if task.state == "active":
            score += 0.3

        # Priority affects urgency
        if task.priority == "high":
            score += 0.2
        elif task.priority == "medium":
            score += 0.1

        return min(score, 1.0)

    @staticmethod
    def _calculate_capability_match(task: Task) -> float:
        """Calculate capability match score.

        Tasks tagged with @autonomous match current capabilities well.
        Development and automation tasks match well.
        """
        tags = set(task.tags)

        # Perfect match: explicitly autonomous
        if "@autonomous" in tags:
            return 1.0

        # Good match: dev, automation, testing
        good_match_tags = {"dev", "automation", "testing", "refactoring"}
        if tags & good_match_tags:
            return 0.9

        # Medium match: research, documentation
        medium_match_tags = {"research", "documentation", "analysis"}
        if tags & medium_match_tags:
            return 0.7

        return 0.5  # Neutral

    @staticmethod
    def _calculate_dependency_value(task: Task) -> float:
        """Calculate dependency value score.

        Tasks that unblock others have higher value.
        For now, just check if task has dependents (would need reverse lookup).
        """
        # TODO: Implement reverse dependency lookup
        # For now, use simple heuristic: infrastructure and automation
        # tasks typically unblock others
        strategic_tags = {"infrastructure", "automation", "testing"}
        tags = set(task.tags)

        if tags & strategic_tags:
            return 0.8

        return 0.5  # Neutral


class MIQPlanner:
    """MIQ-based task planner."""

    @staticmethod
    def calculate_miq_score(task: Task) -> float:
        """Calculate MIQ score for task (wrapper for compatibility).

        Args:
            task: Task to score

        Returns:
            Total MIQ score (0.0-1.0)
        """
        return MIQScore.calculate(task).total

    @staticmethod
    def score_tasks(tasks: list[Task]) -> list[tuple[Task, MIQScore]]:
        """Score multiple tasks with MIQ framework.

        Args:
            tasks: List of tasks to score

        Returns:
            List of (task, score) tuples, sorted by total score descending
        """
        scored = [(task, MIQScore.calculate(task)) for task in tasks]
        scored.sort(key=lambda x: x[1].total, reverse=True)
        return scored

    @staticmethod
    def explain_score(task: Task, score: MIQScore | None = None) -> str:
        """Generate explanation for task's MIQ score.

        Args:
            task: Task being explained
            score: Pre-calculated score (calculates if None)

        Returns:
            Human-readable explanation
        """
        if score is None:
            score = MIQScore.calculate(task)

        explanation = f"""MIQ Score for '{task.id}': {score.total:.2f}

Breakdown:
  Goal Alignment:    {score.goal_alignment:.2f} (30% weight)
  Impact Potential:  {score.impact_potential:.2f} (25% weight)
  Urgency:           {score.urgency:.2f} (20% weight)
  Capability Match:  {score.capability_match:.2f} (15% weight)
  Dependency Value:  {score.dependency_value:.2f} (10% weight)

Strategic Reasoning:
  - Priority: {task.priority}
  - State: {task.state}
  - Tags: {', '.join(task.tags)}
  - Dependencies: {len(task.depends) if task.depends else 0}
"""
        return explanation
