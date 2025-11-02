"""Tests for task executor git commit integration."""

import subprocess
from unittest.mock import MagicMock, patch

from gptme.tasks.executor import TaskExecutor
from gptme.tasks.loader import Task


def test_commit_task_progress_no_changes(tmp_path):
    """Test committing when there are no changes."""
    executor = TaskExecutor(tmp_path)
    task = Task(
        id="test-task",
        title="Test Task",
        state="active",
        priority="medium",
        content="Test content",
        file_path=tmp_path / "test-task.md",
    )

    with patch("subprocess.run") as mock_run:
        # Simulate no changes
        mock_run.return_value = MagicMock(stdout="", returncode=0)

        result = executor.commit_task_progress(task, "test commit")

        # Should return False when no changes
        assert result is False


def test_commit_task_progress_with_changes(tmp_path):
    """Test committing when there are changes."""
    executor = TaskExecutor(tmp_path)
    task = Task(
        id="test-task",
        title="Test Task",
        state="active",
        priority="medium",
        content="Test content",
        file_path=tmp_path / "test-task.md",
    )

    with patch("subprocess.run") as mock_run:
        # First call: check status (has changes)
        # Second call: stage file
        # Third call: commit
        mock_run.side_effect = [
            MagicMock(stdout="M test-task.md\n", returncode=0),  # status
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=0),  # git commit
        ]

        result = executor.commit_task_progress(task, "feat(tasks): update progress")

        # Should return True when commit succeeds
        assert result is True

        # Verify git commands were called
        assert mock_run.call_count == 3


def test_commit_task_progress_git_failure(tmp_path):
    """Test handling of git command failure."""
    executor = TaskExecutor(tmp_path)
    task = Task(
        id="test-task",
        title="Test Task",
        state="active",
        priority="medium",
        content="Test content",
        file_path=tmp_path / "test-task.md",
    )

    with patch("subprocess.run") as mock_run:
        # Simulate git command failure
        mock_run.side_effect = subprocess.CalledProcessError(1, ["git"])

        result = executor.commit_task_progress(task, "test commit")

        # Should return False on git failure
        assert result is False


def test_commit_task_progress_conventional_format(tmp_path):
    """Test that commit message uses conventional commits format."""
    executor = TaskExecutor(tmp_path)
    task = Task(
        id="test-task",
        title="Test Task",
        state="active",
        priority="medium",
        content="Test content",
        file_path=tmp_path / "test-task.md",
    )

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(stdout="M test-task.md\n", returncode=0),  # status
            MagicMock(returncode=0),  # git add
            MagicMock(returncode=0),  # git commit
        ]

        message = "feat(tasks): implement new feature"
        executor.commit_task_progress(task, message)

        # Check that commit was called with proper message
        commit_call = mock_run.call_args_list[2]
        assert "git" in commit_call[0][0]
        assert "commit" in commit_call[0][0]
        assert message in str(commit_call)
