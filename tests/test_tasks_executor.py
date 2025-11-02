"""Tests for task executor with MIQ-guided planning."""

import pytest

from gptme.tasks.executor import ExecutionPlan, TaskExecutor
from gptme.tasks.loader import Task


@pytest.fixture
def sample_task():
    """Create sample task for testing."""
    return Task(
        id="test-task",
        title="Test Task",
        state="new",
        priority="high",
        tags=["dev", "automation"],
        depends=[],
        content="Test task content",
    )


@pytest.fixture
def dev_task():
    """Create dev task for testing."""
    return Task(
        id="dev-task",
        title="Development Task",
        state="active",
        priority="high",
        tags=["dev", "testing"],
        depends=["other-task"],
        content="Implement feature with tests",
    )


@pytest.fixture
def research_task():
    """Create research task for testing."""
    return Task(
        id="research-task",
        title="Research Task",
        state="new",
        priority="medium",
        tags=["research"],
        depends=[],
        content="Research new technology",
    )


class TestExecutionPlan:
    """Tests for ExecutionPlan class."""

    def test_create_execution_plan(self, sample_task):
        """Test creation of execution plan."""
        plan = ExecutionPlan.create(sample_task)

        assert plan.task == sample_task
        assert plan.miq_score is not None
        assert plan.strategy in ["incremental", "deep-focus", "research-heavy"]
        assert len(plan.phases) > 0
        assert len(plan.quality_checks) > 0
        assert plan.estimated_sessions >= 1

    def test_strategy_determination(self, sample_task, research_task):
        """Test strategy determination based on task type."""
        # Dev task with high capability match should be incremental
        dev_plan = ExecutionPlan.create(sample_task)
        assert dev_plan.strategy in ["incremental", "deep-focus"]

        # Research task with lower capability match should be research-heavy
        research_plan = ExecutionPlan.create(research_task)
        assert research_plan.strategy in ["research-heavy", "incremental"]

    def test_phases_include_dev_steps(self, dev_task):
        """Test that dev tasks include appropriate phases."""
        plan = ExecutionPlan.create(dev_task)

        phases_text = " ".join(plan.phases)
        assert "Understand requirements" in phases_text
        assert "Design approach" in phases_text or "Implement" in phases_text
        assert "tests" in phases_text.lower() or "validate" in phases_text.lower()

    def test_quality_checks_for_dev_task(self, dev_task):
        """Test that dev tasks have appropriate quality checks."""
        plan = ExecutionPlan.create(dev_task)

        checks_text = " ".join(plan.quality_checks)
        assert "git" in checks_text.lower()
        assert "pytest" in checks_text.lower() or "tests" in checks_text.lower()

    def test_session_estimation(self, sample_task):
        """Test session estimation based on task complexity."""
        plan = ExecutionPlan.create(sample_task)
        assert plan.estimated_sessions >= 1
        assert plan.estimated_sessions <= 5  # Reasonable upper bound

    def test_format_execution_prompt(self, sample_task):
        """Test execution prompt formatting."""
        plan = ExecutionPlan.create(sample_task)
        prompt = plan.format_execution_prompt()

        # Check prompt includes key sections
        assert sample_task.title in prompt
        assert sample_task.id in prompt
        assert "MIQ Score" in prompt
        assert "Strategy" in prompt
        assert "Phases" in prompt
        assert "Quality Validation" in prompt
        assert sample_task.content in prompt


class TestTaskExecutor:
    """Tests for TaskExecutor class."""

    def test_executor_initialization(self, tmp_path):
        """Test executor initialization."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        executor = TaskExecutor(tasks_dir)
        assert executor.loader is not None
        assert executor.current_task is None
        assert executor.current_plan is None

    def test_format_task_prompt_creates_plan(self, tmp_path, sample_task):
        """Test that format_task_prompt creates execution plan."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        executor = TaskExecutor(tasks_dir)
        executor.current_task = sample_task

        prompt = executor.format_task_prompt()

        # Should have created execution plan
        assert executor.current_plan is not None
        assert executor.current_plan.task == sample_task

        # Prompt should include MIQ-guided content
        assert "MIQ Score" in prompt
        assert "Phases" in prompt

    def test_format_task_prompt_without_task_raises(self, tmp_path):
        """Test that format_task_prompt raises without task."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        executor = TaskExecutor(tasks_dir)

        with pytest.raises(ValueError, match="No task selected"):
            executor.format_task_prompt()

    def test_validate_quality_creates_plan(self, tmp_path, sample_task):
        """Test that validate_quality creates execution plan."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        executor = TaskExecutor(tasks_dir)
        executor.current_task = sample_task

        results = executor.validate_quality()

        # Should have created execution plan
        assert executor.current_plan is not None

        # Should return dict of checks
        assert isinstance(results, dict)
        assert len(results) > 0

    def test_validate_quality_without_task_raises(self, tmp_path):
        """Test that validate_quality raises without task."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        executor = TaskExecutor(tasks_dir)

        with pytest.raises(ValueError, match="No task selected"):
            executor.validate_quality()


class TestExecutionPlanIntegration:
    """Integration tests for execution planning."""

    def test_incremental_strategy_for_high_capability_task(self):
        """Test incremental strategy for high capability match."""
        task = Task(
            id="simple-dev-task",
            title="Simple Dev Task",
            state="active",
            priority="high",
            tags=["dev", "automation"],  # High capability match
            depends=[],
            content="Simple task",
        )

        plan = ExecutionPlan.create(task)

        # High capability match + dev tags = incremental
        assert plan.strategy in ["incremental", "deep-focus"]
        assert plan.estimated_sessions <= 2

    def test_research_heavy_strategy_for_unfamiliar_task(self):
        """Test research-heavy strategy for unfamiliar tasks."""
        task = Task(
            id="unfamiliar-task",
            title="Unfamiliar Task",
            state="new",
            priority="medium",
            tags=["unknown"],  # Lower capability match (no strategic tags)
            depends=[],
            content="Learn completely new technology",
        )

        plan = ExecutionPlan.create(task)

        # Lower capability match = research-heavy or incremental with research phase
        assert plan.strategy in ["research-heavy", "incremental"]

        # Should include research phase for low capability match
        phases_text = " ".join(plan.phases)
        # Low capability match (0.5) should trigger research phase
        assert "research" in phases_text.lower() or len(plan.phases) >= 3

    def test_quality_checks_vary_by_task_type(self):
        """Test that quality checks adapt to task type."""
        # Dev task
        dev_task = Task(
            id="dev",
            title="Dev",
            state="new",
            priority="high",
            tags=["dev"],
            depends=[],
            content="Dev work",
        )
        dev_plan = ExecutionPlan.create(dev_task)
        dev_checks = " ".join(dev_plan.quality_checks)

        # Should include dev-specific checks
        assert "pytest" in dev_checks.lower() or "tests" in dev_checks.lower()

        # Documentation task
        docs_task = Task(
            id="docs",
            title="Docs",
            state="new",
            priority="high",
            tags=["documentation"],
            depends=[],
            content="Write docs",
        )
        docs_plan = ExecutionPlan.create(docs_task)
        docs_checks = " ".join(docs_plan.quality_checks)

        # Should include docs-specific checks
        assert "documentation" in docs_checks.lower()
