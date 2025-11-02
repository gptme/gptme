"""Task execution engine with MIQ-guided planning (Phase 2.3)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loader import Task, TaskLoader
from .planner import MIQPlanner, MIQScore

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    """MIQ-guided execution plan for a task."""

    task: Task
    miq_score: MIQScore
    strategy: str  # "incremental", "deep-focus", "research-heavy"
    phases: list[str]  # Ordered execution phases
    quality_checks: list[str]  # Validation criteria
    estimated_sessions: int  # Based on complexity

    @classmethod
    def create(cls, task: Task) -> "ExecutionPlan":
        """Create execution plan using MIQ framework.

        Args:
            task: Task to plan execution for

        Returns:
            ExecutionPlan with strategy and phases
        """
        score = MIQScore.calculate(task)

        # Determine strategy based on MIQ dimensions
        strategy = cls._determine_strategy(task, score)

        # Break into phases based on complexity
        phases = cls._determine_phases(task, score)

        # Set quality validation criteria
        quality_checks = cls._determine_quality_checks(task)

        # Estimate sessions needed
        estimated_sessions = cls._estimate_sessions(task, score)

        return cls(
            task=task,
            miq_score=score,
            strategy=strategy,
            phases=phases,
            quality_checks=quality_checks,
            estimated_sessions=estimated_sessions,
        )

    @staticmethod
    def _determine_strategy(task: Task, score: MIQScore) -> str:
        """Determine execution strategy based on MIQ dimensions.

        Returns:
            Strategy name: "incremental", "deep-focus", or "research-heavy"
        """
        # High capability match + high impact = incremental approach
        if score.capability_match >= 0.8 and score.impact_potential >= 0.7:
            return "incremental"

        # High goal alignment + high urgency = deep focus
        if score.goal_alignment >= 0.8 and score.urgency >= 0.7:
            return "deep-focus"

        # Lower capability match = research first
        if score.capability_match < 0.6:
            return "research-heavy"

        return "incremental"  # Default

    @staticmethod
    def _determine_phases(task: Task, score: MIQScore) -> list[str]:
        """Determine execution phases based on task and score.

        Returns:
            List of phase descriptions in execution order
        """
        phases = []

        # Phase 1: Always start with understanding
        phases.append("1. Understand requirements and context")

        # Research phase for lower capability match
        if score.capability_match < 0.7:
            phases.append("2. Research solutions and best practices")

        # For dev tasks: design before implementation
        if "dev" in task.tags or "automation" in task.tags:
            phases.append(f"{len(phases)+1}. Design approach and architecture")

        # Implementation phase
        phases.append(f"{len(phases)+1}. Implement solution incrementally")

        # Testing phase for dev tasks
        if "dev" in task.tags or "testing" in task.tags:
            phases.append(f"{len(phases)+1}. Add tests and validate")

        # Documentation phase for all tasks
        if score.impact_potential >= 0.7:  # High impact = needs docs
            phases.append(f"{len(phases)+1}. Document approach and results")

        return phases

    @staticmethod
    def _determine_quality_checks(task: Task) -> list[str]:
        """Determine quality validation criteria for task.

        Returns:
            List of quality checks to perform
        """
        checks = []

        # Universal checks
        checks.append("✓ Work committed to git")
        checks.append("✓ Changes pass pre-commit hooks")

        # Dev-specific checks
        if "dev" in task.tags or "automation" in task.tags:
            checks.append("✓ Tests pass (pytest)")
            checks.append("✓ Type checking passes (mypy)")
            checks.append("✓ Code formatting validated (ruff)")

        # Testing-specific checks
        if "testing" in task.tags:
            checks.append("✓ Test coverage >= baseline")
            checks.append("✓ All tests pass in CI")

        # Documentation-specific checks
        if "documentation" in task.tags:
            checks.append("✓ Documentation builds without errors")
            checks.append("✓ Links validated")

        # Infrastructure-specific checks
        if "infrastructure" in task.tags:
            checks.append("✓ Service health check passes")
            checks.append("✓ No breaking changes to APIs")

        return checks

    @staticmethod
    def _estimate_sessions(task: Task, score: MIQScore) -> int:
        """Estimate number of sessions needed.

        Args:
            task: Task being estimated
            score: MIQ score with complexity indicators

        Returns:
            Estimated number of autonomous sessions
        """
        # Base estimate
        sessions = 1

        # Adjust for capability match (lower = more sessions)
        if score.capability_match < 0.6:
            sessions += 2  # Research + learning
        elif score.capability_match < 0.8:
            sessions += 1  # Some learning

        # Adjust for impact (higher = more careful = more sessions)
        if score.impact_potential >= 0.8:
            sessions += 1

        # Adjust for dependencies (more = more complex)
        if task.depends and len(task.depends) >= 3:
            sessions += 1

        return sessions

    def format_execution_prompt(self) -> str:
        """Format execution plan into prompt for gptme.

        Returns:
            Formatted prompt with execution strategy
        """
        prompt = f"""# Task: {self.task.title}

**Task ID**: {self.task.id}
**MIQ Score**: {self.miq_score.total:.2f}
**Strategy**: {self.strategy}
**Estimated Sessions**: {self.estimated_sessions}

## MIQ Breakdown

{MIQPlanner.explain_score(self.task, self.miq_score)}

## Execution Plan

**Strategy**: {self.strategy}

### Phases

{chr(10).join(self.phases)}

### Quality Validation

Before completing, verify:
{chr(10).join(self.quality_checks)}

## Task Content

{self.task.content}

---

**Instructions**:
- Follow the execution phases in order
- Validate quality checks before completing
- If blocked, document blocker and request help
- Make incremental progress even if full completion not possible
"""
        return prompt


class TaskExecutor:
    """Task executor with MIQ-guided execution planning."""

    def __init__(self, tasks_dir: Path | str):
        """Initialize task executor with task directory."""
        self.loader = TaskLoader(tasks_dir)
        self.current_task: Task | None = None
        self.current_plan: ExecutionPlan | None = None

    def load_tasks(self) -> dict[str, Task]:
        """Load all available tasks."""
        return self.loader.load_all()

    def select_next_task(self, verbose: bool = False) -> Task | None:
        """Select next task using MIQ scoring.

        Args:
            verbose: If True, print MIQ explanations for top tasks

        Returns:
            Selected task or None if no tasks available
        """
        task = self.loader.select_next_task(verbose=verbose)
        if task:
            self.current_task = task
            # Create execution plan
            self.current_plan = ExecutionPlan.create(task)
            logger.info(f"Selected task: {task.id}")
        return task

    def format_task_prompt(self, task: Task | None = None) -> str:
        """Format task into execution prompt with MIQ planning.

        Args:
            task: Task to format (uses current_task if None)

        Returns:
            Formatted prompt with execution plan
        """
        if task is None:
            task = self.current_task

        if task is None:
            raise ValueError("No task selected")

        # Create or reuse execution plan
        if self.current_plan is None or self.current_plan.task != task:
            self.current_plan = ExecutionPlan.create(task)

        return self.current_plan.format_execution_prompt()

    def validate_quality(self, task: Task | None = None) -> dict[str, bool]:
        """Validate quality checks for task.

        Args:
            task: Task to validate (uses current_task if None)

        Returns:
            Dict of check_name -> passed (bool)
        """
        if task is None:
            task = self.current_task

        if task is None:
            raise ValueError("No task selected")

        # Get execution plan
        if self.current_plan is None or self.current_plan.task != task:
            self.current_plan = ExecutionPlan.create(task)

        # TODO: Implement actual validation logic
        # For now, return placeholder
        results = {}
        for check in self.current_plan.quality_checks:
            # Extract check name (after "✓ ")
            check_name = check.split("✓ ", 1)[1] if "✓ " in check else check
            results[check_name] = True  # Placeholder

        return results

    def execute_task(self, task: Task | None = None) -> dict[str, Any]:
        """Execute task using gptme subprocess.

        Args:
            task: Task to execute (uses current_task if None)

        Returns:
            Execution result dictionary with:
                - success: bool
                - output: str (stdout)
                - error: str (stderr)
                - exit_code: int
        """
        import subprocess
        import sys

        if task is None:
            task = self.current_task

        if task is None:
            raise ValueError("No task selected")

        # Get execution prompt
        prompt = self.format_task_prompt(task)

        # Build gptme command
        cmd = [
            sys.executable,
            "-m",
            "gptme",
            "-n",  # Non-interactive mode
            "--no-confirm",  # Skip confirmations
            prompt,
        ]

        logger.info(f"Executing task {task.id} with gptme")

        try:
            # Execute gptme subprocess
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )

            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "error": result.stderr,
                "exit_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error(f"Task {task.id} timed out after 1 hour")
            return {
                "success": False,
                "output": "",
                "error": "Task execution timed out",
                "exit_code": -1,
            }
        except Exception as e:
            logger.error(f"Task {task.id} execution failed: {e}")
            return {
                "success": False,
                "output": "",
                "error": str(e),
                "exit_code": -1,
            }

    def run_loop(
        self,
        max_tasks: int | None = None,
        max_time_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Run task loop, processing tasks until none remain.

        Args:
            max_tasks: Maximum number of tasks to process (None = unlimited)
            max_time_seconds: Maximum execution time in seconds (None = unlimited)

        Returns:
            Summary dictionary with:
                - tasks_attempted: int
                - tasks_completed: int
                - tasks_failed: int
                - execution_time: float (seconds)
        """
        import time

        start_time = time.time()
        tasks_attempted = 0
        tasks_completed = 0
        tasks_failed = 0

        logger.info("Starting task loop")

        while True:
            # Check limits
            if max_tasks and tasks_attempted >= max_tasks:
                logger.info(f"Reached max_tasks limit ({max_tasks})")
                break

            if max_time_seconds:
                elapsed = time.time() - start_time
                if elapsed >= max_time_seconds:
                    logger.info(f"Reached max_time limit ({max_time_seconds}s)")
                    break

            # 1. Select next task
            task = self.select_next_task(verbose=True)
            if not task:
                logger.info("No tasks available")
                break

            tasks_attempted += 1

            # 2. Execute task
            logger.info(f"Executing task {task.id} ({tasks_attempted})")
            result = self.execute_task(task)

            # 3. Check result
            if result["success"]:
                tasks_completed += 1
                logger.info(f"Task {task.id} completed successfully")
            else:
                tasks_failed += 1
                logger.error(f"Task {task.id} failed: {result['error']}")

            # 4. Update task state
            # TODO: Integrate with tracker to update task metadata
            # For now, just log the result

        execution_time = time.time() - start_time

        summary = {
            "tasks_attempted": tasks_attempted,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "execution_time": execution_time,
        }

        logger.info(f"Task loop complete: {summary}")
        return summary
