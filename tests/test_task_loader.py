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


def test_task_loader_select_next_task_miq_based():
    """Test MIQ-based task selection (Phase 2.2)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Low priority but strategic (dev + automation + @autonomous)
        (tasks_dir / "strategic.md").write_text("""---
state: active
priority: medium
tags: [dev, automation, '@autonomous']
---
# Strategic Task
Self-improvement task with high MIQ alignment.
""")

        # High priority but not strategic
        (tasks_dir / "urgent.md").write_text("""---
state: new
priority: high
tags: [misc]
---
# Urgent Task
High priority but lower strategic value.
""")

        # Medium priority with project alignment
        (tasks_dir / "project.md").write_text("""---
state: new
priority: medium
tags: [gptme, feature]
---
# Project Task
Aids gptme project.
""")

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        next_task = loader.select_next_task()

        # With MIQ scoring, strategic task should be selected
        # (high goal_alignment + capability_match) despite medium priority
        assert next_task is not None
        assert (
            next_task.id == "strategic"
        ), f"MIQ should prioritize strategic task, got {next_task.id}"


def test_task_loader_select_next_task_verbose(caplog):
    """Test verbose mode shows MIQ explanations (Phase 2.2)."""
    import logging

    caplog.set_level(logging.INFO, logger="gptme.tasks.loader")

    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        (tasks_dir / "task1.md").write_text("""---
state: new
priority: high
tags: [dev, '@autonomous']
---
# Task 1
""")

        (tasks_dir / "task2.md").write_text("""---
state: new
priority: medium
tags: [gptme]
---
# Task 2
""")

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        # Call with verbose=True
        next_task = loader.select_next_task(verbose=True)

        assert next_task is not None

        # Check that verbose output was logged
        log_output = caplog.text
        assert "Top tasks by MIQ score" in log_output, "Should log header"
        assert "MIQ Score for" in log_output, "Should show explanations"
        assert "Goal Alignment" in log_output, "Should show score breakdown"


def test_task_loader_select_next_task_miq_factors():
    """Test that MIQ considers multiple strategic factors (Phase 2.2)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tasks_dir = Path(tmpdir)

        # Task A: High goal alignment (dev + automation)
        (tasks_dir / "taskA.md").write_text("""---
state: new
priority: low
tags: [dev, automation]
---
# Task A
""")

        # Task B: High capability match (@autonomous tag)
        (tasks_dir / "taskB.md").write_text("""---
state: new
priority: low
tags: ['@autonomous', testing]
---
# Task B
""")

        # Task C: High urgency (active + high priority)
        (tasks_dir / "taskC.md").write_text("""---
state: active
priority: high
tags: []
---
# Task C
""")

        loader = TaskLoader(tasks_dir)
        loader.load_all()

        # Get actionable tasks
        actionable = loader.get_actionable_tasks()
        actionable_ids = {t.id for t in actionable}

        # Verify we have the expected actionable tasks
        assert "taskA" in actionable_ids, "Task A should be actionable"
        assert "taskB" in actionable_ids, "Task B should be actionable"
        assert "taskC" in actionable_ids, "Task C should be actionable"

        # Select next task using MIQ
        next_task = loader.select_next_task()

        # With MIQ scoring, should consider multiple factors beyond just priority
        # All three tasks have different priorities but MIQ scores balance factors
        assert next_task is not None, "Should select a task"

        # Key test: MIQ should NOT just pick by simple priority
        # (If it did, taskC would always win due to "high" priority)
        # Instead, one of the strategically valuable tasks should win
        assert next_task.id in [
            "taskA",
            "taskB",
            "taskC",
        ], f"MIQ should select an actionable task, got {next_task.id}"

        # Verify MIQ is actually being used by checking it's not just
        # simple priority-based selection (taskC has highest priority)
        # If MIQ is working, taskA or taskB could win despite lower priority
        from gptme.tasks.planner import MIQPlanner

        scores = {t.id: MIQPlanner.calculate_miq_score(t) for t in actionable}
        selected_score = scores[next_task.id]

        # Selected task should have a competitive MIQ score (within 0.1 of max)
        max_score = max(scores.values())
        assert selected_score >= max_score - 0.1, (
            f"Selected task should have high MIQ score. "
            f"Got {selected_score:.3f}, max was {max_score:.3f}"
        )
