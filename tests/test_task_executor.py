"""Tests for task execution engine."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.tasks import TaskExecutor


@pytest.fixture
def temp_tasks_dir():
    """Create temporary directory with test tasks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Create test task files
        task1 = tasks_dir / "task1.md"
        task1.write_text("""---
state: new
priority: high
---
# Task 1
This is task 1 content.
""")

        task2 = tasks_dir / "task2.md"
        task2.write_text("""---
state: active
priority: medium
tags: [test, example]
---
# Task 2
This is task 2 content with tags.
""")

        task3 = tasks_dir / "task3.md"
        task3.write_text("""---
state: new
priority: low
depends: [task1]
---
# Task 3
This depends on task1.
""")

        yield tasks_dir


def test_executor_initialization(temp_tasks_dir):
    """Test TaskExecutor initialization."""
    executor = TaskExecutor(temp_tasks_dir)
    assert executor.loader is not None
    assert executor.current_task is None


def test_load_tasks(temp_tasks_dir):
    """Test loading tasks."""
    executor = TaskExecutor(temp_tasks_dir)
    tasks = executor.load_tasks()

    assert len(tasks) == 3
    assert "task1" in tasks
    assert "task2" in tasks
    assert "task3" in tasks


def test_select_next_task(temp_tasks_dir):
    """Test task selection."""
    executor = TaskExecutor(temp_tasks_dir)
    executor.load_tasks()

    task = executor.select_next_task()

    # Should select task1 (highest priority, no dependencies)
    assert task is not None
    assert task.id == "task1"
    assert task.priority == "high"
    assert executor.current_task == task


def test_format_task_prompt(temp_tasks_dir):
    """Test task prompt formatting."""
    executor = TaskExecutor(temp_tasks_dir)
    executor.load_tasks()

    task = executor.loader.get_task("task1")
    assert task is not None

    prompt = executor.format_task_prompt(task)

    assert "# Task: Task 1" in prompt
    assert "**Task ID**: task1" in prompt
    assert "**State**: new" in prompt
    assert "**Priority**: high" in prompt
    assert "This is task 1 content" in prompt


def test_create_task_message(temp_tasks_dir):
    """Test message creation from task."""
    executor = TaskExecutor(temp_tasks_dir)
    executor.load_tasks()

    task = executor.loader.get_task("task1")
    assert task is not None

    message = executor.create_task_message(task)

    assert message.role == "user"
    assert "# Task: Task 1" in message.content
    assert "task1" in message.content


def test_execute_task(temp_tasks_dir):
    """Test task execution (Phase 1 basic version)."""
    executor = TaskExecutor(temp_tasks_dir)
    executor.load_tasks()

    task = executor.loader.get_task("task1")
    assert task is not None

    # Mock subprocess.run to prevent actual gptme execution
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Task completed successfully",
            stderr="",
        )

        # For Phase 1, just check that execution doesn't fail
        result = executor.execute_task(task)
        assert result["success"] is True


def test_run_loop_no_tasks():
    """Test run loop with no tasks."""
    with tempfile.TemporaryDirectory() as tmpdir:
        empty_dir = Path(tmpdir)
        executor = TaskExecutor(empty_dir)

        # Should handle empty directory gracefully
        executor.run_loop()  # Should not raise


def test_run_loop_with_tasks(temp_tasks_dir):
    """Test run loop with tasks."""
    executor = TaskExecutor(temp_tasks_dir)

    # Load tasks before running loop
    executor.load_tasks()

    # Should execute without errors
    executor.run_loop()

    # Should have selected a task
    assert executor.current_task is not None
    assert executor.current_task.id == "task1"
