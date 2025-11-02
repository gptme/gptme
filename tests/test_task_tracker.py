"""Tests for task progress tracking functionality."""

import tempfile
from datetime import datetime
from pathlib import Path

from gptme.tasks import Task, TaskProgress, TaskProgressTracker


def test_parse_subtasks_empty():
    """Test parsing subtasks from content with no checkboxes."""
    tracker = TaskProgressTracker("/tmp")
    content = "# Task\n\nNo subtasks here."

    completed, total = tracker.parse_subtasks(content)

    assert completed == 0
    assert total == 0


def test_parse_subtasks_all_pending():
    """Test parsing subtasks with all pending."""
    tracker = TaskProgressTracker("/tmp")
    content = """# Task

## Subtasks
- [ ] First task
- [ ] Second task
- [ ] Third task
"""

    completed, total = tracker.parse_subtasks(content)

    assert completed == 0
    assert total == 3


def test_parse_subtasks_mixed():
    """Test parsing subtasks with mixed completion."""
    tracker = TaskProgressTracker("/tmp")
    content = """# Task

## Subtasks
- [x] Done task
- [ ] Pending task
- [X] Another done (capital X)
- [ ] Another pending
"""

    completed, total = tracker.parse_subtasks(content)

    assert completed == 2
    assert total == 4


def test_parse_subtasks_all_done():
    """Test parsing subtasks with all completed."""
    tracker = TaskProgressTracker("/tmp")
    content = """# Task

## Subtasks
- [x] First
- [x] Second
- [X] Third (capital X)
"""

    completed, total = tracker.parse_subtasks(content)

    assert completed == 3
    assert total == 3


def test_task_progress_completion_percentage():
    """Test TaskProgress completion percentage calculation."""
    progress = TaskProgress(
        completed_subtasks=3,
        total_subtasks=10,
        last_execution="2025-11-02T14:00:00Z",
    )

    assert progress.completion_percentage == 30
    assert progress.progress_string == "3/10"


def test_task_progress_zero_total():
    """Test TaskProgress with zero total subtasks."""
    progress = TaskProgress(
        completed_subtasks=0,
        total_subtasks=0,
        last_execution="2025-11-02T14:00:00Z",
    )

    assert progress.completion_percentage == 0
    assert progress.progress_string == "0/0"


def test_get_progress():
    """Test getting progress from task."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""---
state: active
last_execution: 2025-11-02T13:00:00Z
---

# Test Task

## Subtasks
- [x] First
- [ ] Second
- [ ] Third
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)
        tracker = TaskProgressTracker(path.parent)

        progress = tracker.get_progress(task)

        assert progress.completed_subtasks == 1
        assert progress.total_subtasks == 3
        assert progress.completion_percentage == 33
        # YAML parser converts ISO 8601 to datetime, check type
        assert progress.last_execution  # Just verify it exists
    finally:
        path.unlink()


def test_update_task_metadata():
    """Test updating task metadata with progress."""
    task = Task(
        id="test-task",
        title="Test",
        content="content",
        metadata={},
    )

    progress = TaskProgress(
        completed_subtasks=5,
        total_subtasks=10,
        last_execution="2025-11-02T13:00:00Z",
    )

    tracker = TaskProgressTracker("/tmp")
    execution_start = datetime(2025, 11, 2, 13, 0, 0)

    updated = tracker.update_task_metadata(task, progress, execution_start)

    assert updated.metadata["progress"] == "5/10"
    assert "last_execution" in updated.metadata
    assert "execution_time" in updated.metadata


def test_save_task_with_frontmatter():
    """Test saving task with existing frontmatter."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""---
state: active
priority: high
---

# Test Task
Content here.
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)
        task.metadata["progress"] = "3/10"
        task.metadata["last_execution"] = "2025-11-02T14:00:00Z"

        tracker = TaskProgressTracker(path.parent)
        tracker.save_task(task)

        # Reload and verify
        updated_content = path.read_text()
        assert "progress: 3/10" in updated_content
        assert "last_execution: " in updated_content
        assert "Test Task" in updated_content
    finally:
        path.unlink()


def test_save_task_without_frontmatter():
    """Test saving task without existing frontmatter."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""# Test Task
Content without frontmatter.
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)
        task.metadata["progress"] = "1/5"

        tracker = TaskProgressTracker(path.parent)
        tracker.save_task(task)

        # Reload and verify
        updated_content = path.read_text()
        assert updated_content.startswith("---\n")
        assert "progress: 1/5" in updated_content
    finally:
        path.unlink()


def test_update_and_save():
    """Test full update and save workflow."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("""---
state: active
---

# Test Task

## Subtasks
- [x] First
- [x] Second
- [ ] Third
""")
        f.flush()
        path = Path(f.name)

    try:
        task = Task.from_file(path)
        tracker = TaskProgressTracker(path.parent)
        execution_start = datetime(2025, 11, 2, 14, 0, 0)

        progress = tracker.update_and_save(task, execution_start)

        # Check returned progress
        assert progress.completed_subtasks == 2
        assert progress.total_subtasks == 3
        assert progress.completion_percentage == 66

        # Reload file and verify
        updated_task = Task.from_file(path)
        assert updated_task.metadata["progress"] == "2/3"
        assert "last_execution" in updated_task.metadata
        assert "execution_time" in updated_task.metadata
    finally:
        path.unlink()


def test_generate_progress_report():
    """Test generating progress report."""
    # Create test tasks
    task1 = Task(
        id="task1",
        title="First Task",
        content="- [x] Done",
        state="done",
        metadata={"progress": "1/1"},
    )

    task2 = Task(
        id="task2",
        title="Second Task",
        content="- [x] First\n- [ ] Second",
        state="active",
        metadata={"progress": "1/2"},
    )

    task3 = Task(
        id="task3",
        title="Blocked Task",
        content="- [ ] Pending",
        state="active",
        metadata={"progress": "0/1", "blockers": ["Missing API key"]},
    )

    tracker = TaskProgressTracker("/tmp")
    session_start = datetime(2025, 11, 2, 14, 0, 0)
    session_end = datetime(2025, 11, 2, 14, 30, 0)

    report = tracker.generate_progress_report(
        [task1, task2, task3],
        session_start,
        session_end,
    )

    # Verify report content
    assert "Task Loop Progress Report" in report
    assert "2025-11-02 14:00-14:30" in report
    assert "30m" in report  # Duration
    assert "Completed Tasks" in report
    assert "task1" in report
    assert "In Progress" in report
    assert "task2" in report
    assert "Blocked" in report
    assert "task3" in report
    assert "Missing API key" in report


def test_generate_progress_report_empty():
    """Test generating progress report with no tasks."""
    tracker = TaskProgressTracker("/tmp")
    session_start = datetime(2025, 11, 2, 14, 0, 0)
    session_end = datetime(2025, 11, 2, 14, 15, 0)

    report = tracker.generate_progress_report(
        [],
        session_start,
        session_end,
    )

    assert "Task Loop Progress Report" in report
    assert "15m" in report
    assert "**Tasks**: 0" in report
