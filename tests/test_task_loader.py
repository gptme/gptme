"""Tests for task loading functionality."""

import tempfile
from pathlib import Path

import pytest

from gptme.tasks import Task, TaskLoader


def test_task_from_file_with_frontmatter():
    """Test loading task from file with YAML frontmatter."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""---
state: active
priority: high
tags:
- test
- automation
depends:
- other-task
---

# Test Task

This is a test task with some content.

## Subtasks
- [ ] First subtask
- [x] Done subtask
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)

        assert task.id == path.stem
        assert task.title == "Test Task"
        assert task.state == "active"
        assert task.priority == "high"
        assert "test" in task.tags
        assert "automation" in task.tags
        assert "other-task" in task.depends
        assert "This is a test task" in task.content
    finally:
        path.unlink()


def test_task_from_file_without_frontmatter():
    """Test loading task from file without frontmatter."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""# Simple Task

Just a simple task without frontmatter.
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)

        assert task.title == "Simple Task"
        assert task.state == "new"  # default
        assert task.priority == "medium"  # default
        assert task.tags == []
        assert task.depends == []
    finally:
        path.unlink()


def test_task_loader_load_all():
    """Test loading all tasks from directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Create test tasks
        (tasks_dir / "task1.md").write_text("""---
state: new
priority: high
---
# Task 1
""")

        (tasks_dir / "task2.md").write_text("""---
state: active
priority: medium
---
# Task 2
""")

        (tasks_dir / "task3.md").write_text("""---
state: done
priority: low
---
# Task 3
""")

        loader = TaskLoader(tasks_dir)
        tasks = loader.load_all()

        assert len(tasks) == 3
        assert "task1" in tasks
        assert "task2" in tasks
        assert "task3" in tasks


def test_task_loader_filter_by_state():
    """Test filtering tasks by state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        (tasks_dir / "task1.md").write_text("---\nstate: new\n---\n# Task 1")
        (tasks_dir / "task2.md").write_text("---\nstate: active\n---\n# Task 2")
        (tasks_dir / "task3.md").write_text("---\nstate: done\n---\n# Task 3")

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        new_tasks = loader.filter_by_state("new")
        assert len(new_tasks) == 1
        assert new_tasks[0].id == "task1"

        active_tasks = loader.filter_by_state("active")
        assert len(active_tasks) == 1
        assert active_tasks[0].id == "task2"


def test_task_loader_get_actionable_tasks():
    """Test getting actionable tasks (no blocking dependencies)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Task with no dependencies
        (tasks_dir / "task1.md").write_text(
            "---\nstate: new\npriority: high\n---\n# Task 1"
        )

        # Task depending on done task (actionable)
        (tasks_dir / "task2.md").write_text(
            "---\nstate: new\ndepends: [task3]\n---\n# Task 2"
        )
        (tasks_dir / "task3.md").write_text("---\nstate: done\n---\n# Task 3")

        # Task depending on active task (not actionable)
        (tasks_dir / "task4.md").write_text(
            "---\nstate: new\ndepends: [task5]\n---\n# Task 4"
        )
        (tasks_dir / "task5.md").write_text("---\nstate: active\n---\n# Task 5")

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        actionable = loader.get_actionable_tasks()
        actionable_ids = {t.id for t in actionable}

        assert "task1" in actionable_ids  # no dependencies
        assert "task2" in actionable_ids  # depends on done task
        assert "task4" not in actionable_ids  # depends on active task
        assert "task5" in actionable_ids  # active tasks are actionable


def test_task_loader_select_next_task():
    """Test selecting next task based on priority."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        (tasks_dir / "low.md").write_text(
            "---\nstate: new\npriority: low\n---\n# Low Priority"
        )
        (tasks_dir / "high.md").write_text(
            "---\nstate: new\npriority: high\n---\n# High Priority"
        )
        (tasks_dir / "medium.md").write_text(
            "---\nstate: new\npriority: medium\n---\n# Medium Priority"
        )

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        next_task = loader.select_next_task()

        assert next_task is not None
        assert next_task.id == "high"  # highest priority


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
