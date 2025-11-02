"""Task execution engine for task loop mode."""

import logging
import subprocess
from pathlib import Path

from ..message import Message
from .loader import Task, TaskLoader

logger = logging.getLogger(__name__)


class TaskExecutor:
    """Executes tasks using gptme's message handling."""

    def __init__(self, tasks_dir: Path | str):
        """Initialize task executor with task directory."""
        self.loader = TaskLoader(tasks_dir)
        self.current_task: Task | None = None

    def load_tasks(self) -> dict[str, Task]:
        """Load all available tasks."""
        return self.loader.load_all()

    def select_next_task(self) -> Task | None:
        """Select next task to work on."""
        task = self.loader.select_next_task()
        if task:
            self.current_task = task
            logger.info(f"Selected task: {task.id}")
        return task

    def format_task_prompt(self, task: Task) -> str:
        """Format task into execution prompt."""
        prompt = f"""# Task: {task.title}

**Task ID**: {task.id}
**State**: {task.state}
**Priority**: {task.priority}

## Task Content

{task.content}

---

Please work on this task. Follow the task description and complete the subtasks.
When done, update progress and commit your changes.
"""
        return prompt

    def create_task_message(self, task: Task) -> Message:
        """Create Message object from task."""
        prompt = self.format_task_prompt(task)
        return Message(role="user", content=prompt)

    def commit_task_progress(self, task: Task, message: str) -> bool:
        """Commit task progress to git.

        Args:
            task: Task that was worked on
            message: Commit message (should use conventional commits format)

        Returns:
            bool: True if commit successful, False if no changes or failure
        """
        try:
            # Check if there are changes
            status_result = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                capture_output=True,
                text=True,
                check=True,
            )

            if not status_result.stdout.strip():
                logger.info("No changes to commit")
                return False

            # Stage the task file if it exists
            if task.file_path:
                task_file = str(task.file_path)
                subprocess.run(
                    ["git", "add", task_file],
                    check=True,
                )
                logger.info(f"Staged {task_file}")

            # Create commit
            subprocess.run(
                ["git", "commit", "-m", message],
                check=True,
            )
            logger.info(f"Created commit: {message}")

            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Git command failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error committing task progress: {e}")
            return False

    def execute_task(self, task: Task) -> bool:
        """Execute a single task.

        Returns:
            bool: True if task completed successfully, False otherwise
        """
        logger.info(f"Executing task: {task.id}")

        # For Phase 1, we create the message but don't execute yet
        # Full execution will be integrated with gptme's message loop in next step
        message = self.create_task_message(task)
        logger.info(f"Created execution prompt for task: {task.id}")
        logger.debug(f"Prompt preview: {message.content[:200]}...")

        # TODO: Integrate with gptme's message handling loop
        # This will be implemented in next phase

        return True

    def run_loop(self) -> None:
        """Run task execution loop."""
        logger.info("Starting task loop mode")

        # Load tasks
        tasks = self.load_tasks()
        logger.info(f"Loaded {len(tasks)} tasks")

        if not tasks:
            logger.warning("No tasks found")
            return

        # Select next task
        task = self.select_next_task()
        if not task:
            logger.info("No actionable tasks available")
            return

        # Execute task
        success = self.execute_task(task)

        if success:
            logger.info(f"Task {task.id} execution initiated")
        else:
            logger.error(f"Task {task.id} execution failed")
