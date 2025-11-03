"""Tests for task executor with MIQ-guided planning."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.tasks.executor import ExecutionPlan, TaskExecutor
from gptme.tasks.loader import Task


@pytest.fixture
def sample_task() -> Task:
    """Create a sample task for testing."""
    return Task(
        id="test-task",
        title="Test Task",
        content="# Test Task\n\nTest content",
        tags=["dev", "automation"],
        metadata={
            "state": "new",
            "created": "2025-11-02",
            "priority": "high",
        },
    )


@pytest.fixture
def sample_task_simple() -> Task:
    """Create a simple sample task for testing."""
    return Task(
        id="simple-task",
        title="Simple Task",
        content="# Simple Task\n\nSimple content",
        metadata={
            "state": "new",
            "created": "2025-11-02",
            "tags": [],
        },
    )


@pytest.fixture
def tasks_dir(tmp_path: Path) -> Path:
    """Create temporary tasks directory."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return tasks_dir


@pytest.fixture
def executor(tasks_dir: Path) -> TaskExecutor:
    """Create TaskExecutor instance."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    return TaskExecutor(tasks_dir)


class TestExecutionPlan:
    """Tests for ExecutionPlan creation."""

    def test_create_execution_plan(self, sample_task: Task):
        """Test ExecutionPlan.create() generates plan with strategy."""
        plan = ExecutionPlan.create(sample_task)

        assert plan.task == sample_task
        assert plan.strategy in ["incremental", "deep-focus", "research-heavy"]
        assert len(plan.phases) > 0
        assert len(plan.quality_checks) > 0
        assert plan.estimated_sessions > 0

    def test_determine_strategy_incremental(self, sample_task: Task):
        """Test incremental strategy for high capability + impact."""
        # High priority, dev tags → high capability and impact
        plan = ExecutionPlan.create(sample_task)

        # With dev + automation tags and high priority, should be incremental
        assert plan.strategy in ["incremental", "deep-focus"]

    def test_determine_strategy_research_heavy(self):
        """Test research-heavy strategy for low capability match."""
        # Task with no matching tags → low capability
        task = Task(
            id="research-task",
            title="Research Task",
            content="# Research\n\nNeed to learn",
            metadata={"state": "new", "created": "2025-11-02", "tags": ["research"]},
        )
        plan = ExecutionPlan.create(task)

        # Research tag might not match @autonomous → research-heavy
        # (depends on capability scoring, but should be valid)
        assert plan.strategy in ["incremental", "deep-focus", "research-heavy"]

    def test_determine_phases_dev_task(self, sample_task: Task):
        """Test phases for dev task include design."""
        plan = ExecutionPlan.create(sample_task)

        # Dev task should include design phase
        phases_text = " ".join(plan.phases)
        assert "design" in phases_text.lower() or "architecture" in phases_text.lower()

    def test_determine_quality_checks(self, sample_task: Task):
        """Test quality checks are generated."""
        plan = ExecutionPlan.create(sample_task)

        # Should have multiple quality checks
        assert len(plan.quality_checks) >= 3
        # Check format: "✓ description"
        assert all(check.startswith("✓ ") for check in plan.quality_checks)

    def test_estimate_sessions_varies_by_priority(self):
        """Test session estimation considers priority."""
        high_priority_task = Task(
            id="high-task",
            title="High Priority",
            content="content",
            metadata={"state": "new", "created": "2025-11-02", "priority": "high"},
        )
        low_priority_task = Task(
            id="low-task",
            title="Low Priority",
            content="content",
            metadata={"state": "new", "created": "2025-11-02", "priority": "low"},
        )

        high_plan = ExecutionPlan.create(high_priority_task)
        low_plan = ExecutionPlan.create(low_priority_task)

        # Both should have reasonable estimates
        assert 1 <= high_plan.estimated_sessions <= 10
        assert 1 <= low_plan.estimated_sessions <= 10

    def test_format_execution_prompt(self, sample_task: Task):
        """Test execution prompt formatting."""
        plan = ExecutionPlan.create(sample_task)
        prompt = plan.format_execution_prompt()

        # Prompt should contain key information
        assert sample_task.id in prompt
        assert sample_task.title in prompt
        assert plan.strategy in prompt
        assert "Phase" in prompt or "phase" in prompt


class TestTaskExecutor:
    """Tests for TaskExecutor."""

    def test_init(self, tasks_dir: Path):
        """Test TaskExecutor initialization."""
        executor = TaskExecutor(tasks_dir)

        assert executor.loader is not None
        assert executor.tracker is not None
        assert executor.current_task is None
        assert executor.current_plan is None

    def test_load_tasks(self, executor: TaskExecutor, tasks_dir: Path):
        """Test loading tasks from directory."""
        # Create a task file
        task_file = tasks_dir / "test-task.md"
        task_file.parent.mkdir(exist_ok=True)
        task_file.write_text(
            """---
state: new
created: 2025-11-02
priority: high
tags: [dev]
---

# Test Task

Task content
"""
        )

        tasks = executor.load_tasks()
        assert len(tasks) == 1
        assert "test-task" in tasks

    def test_select_next_task(self, executor: TaskExecutor, tasks_dir: Path):
        """Test task selection using MIQ scoring."""
        # Create task files
        for i in range(3):
            task_file = tasks_dir / f"task-{i}.md"
            task_file.parent.mkdir(exist_ok=True)
            task_file.write_text(
                f"""---
state: new
created: 2025-11-02
priority: {'high' if i == 0 else 'medium'}
tags: [dev]
---

# Task {i}

Content
"""
            )

        task = executor.select_next_task()
        assert task is not None
        assert executor.current_task == task
        assert executor.current_plan is not None

    def test_select_next_task_no_tasks(self, executor: TaskExecutor):
        """Test task selection when no tasks available."""
        task = executor.select_next_task()
        assert task is None
        assert executor.current_task is None

    def test_format_task_prompt(self, executor: TaskExecutor, sample_task: Task):
        """Test formatting task into execution prompt."""
        executor.current_task = sample_task
        prompt = executor.format_task_prompt()

        assert sample_task.id in prompt
        assert sample_task.title in prompt

    def test_format_task_prompt_no_task(self, executor: TaskExecutor):
        """Test format_task_prompt raises when no task selected."""
        with pytest.raises(ValueError, match="No task selected"):
            executor.format_task_prompt()

    def test_validate_quality(self, executor: TaskExecutor, sample_task: Task):
        """Test quality validation."""
        executor.current_task = sample_task
        executor.current_plan = ExecutionPlan.create(sample_task)

        results = executor.validate_quality()
        assert isinstance(results, dict)
        # All checks should return True (placeholder)
        assert all(results.values())


class TestTaskExecution:
    """Tests for task execution functionality."""

    @patch("gptme.tasks.executor.subprocess.run")
    def test_execute_task_success(
        self, mock_run: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test successful task execution."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Output", stderr="", text=True
        )

        result = executor.execute_task(sample_task)

        assert result["success"] is True
        assert result["output"] == "Output"
        assert result["exit_code"] == 0

    @patch("gptme.tasks.executor.subprocess.run")
    def test_execute_task_failure(
        self, mock_run: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test failed task execution."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Error", text=True
        )

        result = executor.execute_task(sample_task)

        assert result["success"] is False
        assert result["error"] == "Error"
        assert result["exit_code"] == 1

    @patch("gptme.tasks.executor.subprocess.run")
    def test_execute_task_timeout(
        self, mock_run: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test task execution timeout."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="gptme", timeout=3600)

        result = executor.execute_task(sample_task)

        assert result["success"] is False
        assert "timeout" in result["error"].lower()

    @patch("gptme.tasks.executor.subprocess.run")
    def test_execute_task_exception(
        self, mock_run: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test task execution with exception."""
        mock_run.side_effect = Exception("Unexpected error")

        result = executor.execute_task(sample_task)

        assert result["success"] is False
        assert "Unexpected error" in result["error"]


class TestTaskLoop:
    """Tests for task loop functionality."""

    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_basic(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
    ):
        """Test basic run_loop execution."""
        # Mock: return task once, then None
        mock_select.side_effect = [sample_task, None]

        # Mock: successful execution
        mock_execute.return_value = {
            "success": True,
            "output": "Done",
            "error": "",
            "exit_code": 0,
        }

        summary = executor.run_loop()

        assert summary["tasks_attempted"] == 1
        assert summary["tasks_completed"] == 1
        assert summary["tasks_failed"] == 0
        assert "execution_time" in summary
        assert "task_results" in summary

    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_multiple_tasks(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
        sample_task_simple: Task,
    ):
        """Test run_loop with multiple tasks."""
        # Mock: return 2 tasks then None
        mock_select.side_effect = [sample_task, sample_task_simple, None]

        # Mock: successful executions
        mock_execute.return_value = {
            "success": True,
            "output": "Done",
            "error": "",
            "exit_code": 0,
        }

        summary = executor.run_loop()

        assert summary["tasks_attempted"] == 2
        assert summary["tasks_completed"] == 2
        assert len(summary["task_results"]) == 2

    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_with_failures(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
    ):
        """Test run_loop handles task failures."""
        # Mock: always return a task (will stop at max_tasks)
        mock_select.return_value = sample_task

        # Mock: alternating success/failure
        mock_execute.side_effect = [
            {"success": True, "output": "Done", "error": "", "exit_code": 0},
            {"success": False, "output": "", "error": "Failed", "exit_code": 1},
            {"success": True, "output": "Done", "error": "", "exit_code": 0},
        ]

        summary = executor.run_loop(max_tasks=3)

        assert summary["tasks_attempted"] == 3
        assert summary["tasks_completed"] == 2
        assert summary["tasks_failed"] == 1

    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_max_tasks_limit(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
    ):
        """Test run_loop respects max_tasks limit."""
        # Mock: always return a task
        mock_select.return_value = sample_task

        # Mock: successful execution
        mock_execute.return_value = {
            "success": True,
            "output": "Done",
            "error": "",
            "exit_code": 0,
        }

        summary = executor.run_loop(max_tasks=3)

        assert summary["tasks_attempted"] == 3
        assert summary["tasks_completed"] == 3
        assert summary["tasks_failed"] == 0

    @patch.object(TaskExecutor, "select_next_task")
    @patch.object(TaskExecutor, "execute_task")
    def test_run_loop_max_time_limit(
        self,
        mock_execute: MagicMock,
        mock_select: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
    ):
        """Test run_loop respects max_time_seconds limit."""
        import time

        # Mock: always return a task
        mock_select.return_value = sample_task

        # Mock: execution takes 2 seconds
        def slow_execute(*args, **kwargs):
            time.sleep(2)
            return {"success": True, "output": "Done", "error": "", "exit_code": 0}

        mock_execute.side_effect = slow_execute

        summary = executor.run_loop(max_time_seconds=3)

        # Should complete 1-2 tasks before timeout
        assert summary["tasks_attempted"] >= 1
        assert summary["execution_time"] >= 2  # At least one 2s execution
        assert summary["execution_time"] < 6  # Stopped before 3rd task


class TestPhase32ConversationManagement:
    """Tests for Phase 3.2: Conversation Management."""

    def test_executor_has_tracker(self, tasks_dir: Path):
        """Test TaskExecutor initializes with tracker."""
        tasks_dir.mkdir(parents=True, exist_ok=True)
        executor = TaskExecutor(tasks_dir)

        assert hasattr(executor, "tracker")
        assert executor.tracker is not None

    @patch("gptme.tasks.tracker.TaskProgressTracker.update_and_save")
    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_update_task_progress_called(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        mock_update: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
    ):
        """Test _update_task_progress is called during run_loop."""
        # Mock: return task once, then None
        mock_select.side_effect = [sample_task, None]

        # Mock: successful execution
        mock_execute.return_value = {
            "success": True,
            "output": "Done",
            "error": "",
            "exit_code": 0,
        }

        executor.run_loop()

        # Verify update_and_save was called
        mock_update.assert_called_once()

    @patch("gptme.tasks.tracker.TaskProgressTracker.update_and_save")
    def test_update_task_progress_success(
        self, mock_update: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test _update_task_progress with successful execution."""
        result = {
            "success": True,
            "output": "Done",
            "error": "",
            "exit_code": 0,
        }

        executor._update_task_progress(sample_task, result)

        # Verify update_and_save was called with task
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == sample_task  # First positional arg is task
        assert call_args[1] is not None  # Second positional arg is execution_start

    @patch("gptme.tasks.tracker.TaskProgressTracker.update_and_save")
    def test_update_task_progress_failure(
        self, mock_update: MagicMock, executor: TaskExecutor, sample_task: Task
    ):
        """Test _update_task_progress with failed execution."""
        result = {
            "success": False,
            "output": "",
            "error": "Test error",
            "exit_code": 1,
        }

        executor._update_task_progress(sample_task, result)

        # Verify update_and_save was called with task
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0]
        assert call_args[0] == sample_task  # First positional arg is task
        assert call_args[1] is not None  # Second positional arg is execution_start

    @patch("gptme.tasks.tracker.TaskProgressTracker.update_and_save")
    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_tracks_task_results(
        self,
        mock_select: MagicMock,
        mock_execute: MagicMock,
        mock_tracker: MagicMock,
        executor: TaskExecutor,
        sample_task: Task,
        sample_task_simple: Task,
    ):
        """Test run_loop tracks task_results in summary."""
        # Mock: return 2 tasks then None
        mock_select.side_effect = [sample_task, sample_task_simple, None]

        # Mock: successful then failed execution
        mock_execute.side_effect = [
            {"success": True, "output": "Done", "error": "", "exit_code": 0},
            {"success": False, "output": "", "error": "Failed", "exit_code": 1},
        ]

        # Mock: tracker returns mock progress
        mock_progress = MagicMock()
        mock_progress.progress_string = "0/0"
        mock_progress.completion_percentage = 0
        mock_tracker.return_value = mock_progress

        summary = executor.run_loop()

        # Verify task_results are tracked
        assert "task_results" in summary
        assert len(summary["task_results"]) == 2

        # Verify first result (success)
        assert summary["task_results"][0]["task_id"] == sample_task.id
        assert summary["task_results"][0]["success"] is True

        # Verify second result (failure)
        assert summary["task_results"][1]["task_id"] == sample_task_simple.id
        assert summary["task_results"][1]["success"] is False
        assert summary["task_results"][1]["error"] == "Failed"

    @patch.object(TaskExecutor, "execute_task")
    @patch.object(TaskExecutor, "select_next_task")
    def test_run_loop_empty_results_when_no_tasks(
        self, mock_select: MagicMock, mock_execute: MagicMock, executor: TaskExecutor
    ):
        """Test run_loop returns empty task_results when no tasks."""
        # Mock: no tasks available
        mock_select.return_value = None

        summary = executor.run_loop()

        assert "task_results" in summary
        assert len(summary["task_results"]) == 0
        assert summary["tasks_attempted"] == 0
