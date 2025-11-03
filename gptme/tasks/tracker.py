"""Progress tracking for task loop mode."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .loader import Task

logger = logging.getLogger(__name__)


@dataclass
class TaskProgress:
    """Represents task progress state."""

    completed_subtasks: int
    total_subtasks: int
    last_execution: str
    execution_time: str | None = None
    blockers: list[str] | None = None
    next_action: str | None = None

    @property
    def completion_percentage(self) -> int:
        """Calculate completion percentage."""
        if self.total_subtasks == 0:
            return 0
        return int((self.completed_subtasks / self.total_subtasks) * 100)

    @property
    def progress_string(self) -> str:
        """Format progress as string: '3/10'."""
        return f"{self.completed_subtasks}/{self.total_subtasks}"


class TaskProgressTracker:
    """Tracks and updates task progress."""

    def __init__(self, tasks_dir: Path | str):
        """Initialize progress tracker."""
        self.tasks_dir = Path(tasks_dir)
        logger.info(f"TaskProgressTracker initialized: {self.tasks_dir}")

    def parse_subtasks(self, content: str) -> tuple[int, int]:
        """Parse completed and total subtasks from content.

        Args:
            content: Task content with markdown checkboxes

        Returns:
            Tuple of (completed_count, total_count)
        """
        # Match markdown checkboxes: - [ ] or - [x] or - [X]
        unchecked = re.findall(r"^- \[ \]", content, re.MULTILINE)
        checked = re.findall(r"^- \[[xX]\]", content, re.MULTILINE)

        completed = len(checked)
        total = completed + len(unchecked)

        logger.debug(f"Parsed subtasks: {completed}/{total}")
        return completed, total

    def get_progress(self, task: Task) -> TaskProgress:
        """Get current progress for a task.

        Args:
            task: Task to get progress for

        Returns:
            TaskProgress object with current state
        """
        completed, total = self.parse_subtasks(task.content)

        return TaskProgress(
            completed_subtasks=completed,
            total_subtasks=total,
            last_execution=task.metadata.get("last_execution", ""),
            execution_time=task.metadata.get("execution_time"),
            blockers=task.metadata.get("blockers", []),
            next_action=task.metadata.get("next_action"),
        )

    def update_task_metadata(
        self,
        task: Task,
        progress: TaskProgress,
        execution_start: datetime | None = None,
    ) -> Task:
        """Update task metadata with progress information.

        Args:
            task: Task to update
            progress: Current progress state
            execution_start: Optional start time to calculate duration

        Returns:
            Updated task object
        """
        # Update metadata
        task.metadata["progress"] = progress.progress_string
        task.metadata["last_execution"] = datetime.now().isoformat()

        if execution_start:
            duration = datetime.now() - execution_start
            minutes = int(duration.total_seconds() / 60)
            task.metadata["execution_time"] = f"{minutes}m"

        if progress.blockers:
            task.metadata["blockers"] = progress.blockers

        if progress.next_action:
            task.metadata["next_action"] = progress.next_action

        logger.info(
            f"Updated task {task.id} metadata: progress={progress.progress_string}"
        )
        return task

    def save_task(self, task: Task) -> None:
        """Save updated task to file.

        Args:
            task: Task to save
        """
        if not task.file_path:
            # Skip saving for tasks without file_path (e.g., test tasks)
            return

        # Read original content
        original = task.file_path.read_text()

        # Split into frontmatter and body
        if original.startswith("---\n"):
            parts = original.split("---\n", 2)
            if len(parts) >= 3:
                # Update frontmatter
                updated_frontmatter = yaml.dump(
                    task.metadata, default_flow_style=False, sort_keys=False
                )
                # Reconstruct file
                updated = f"---\n{updated_frontmatter}---\n{parts[2]}"
            else:
                # Malformed frontmatter, just update metadata
                logger.warning(f"Malformed frontmatter in {task.file_path}")
                updated = self._create_with_frontmatter(task)
        else:
            # No frontmatter, add it
            updated = self._create_with_frontmatter(task)

        # Write back
        task.file_path.write_text(updated)
        logger.info(f"Saved task {task.id} to {task.file_path}")

    def _create_with_frontmatter(self, task: Task) -> str:
        """Create file content with frontmatter and body."""
        frontmatter = yaml.dump(
            task.metadata, default_flow_style=False, sort_keys=False
        )
        return f"---\n{frontmatter}---\n{task.content}"

    def update_and_save(
        self, task: Task, execution_start: datetime | None = None
    ) -> TaskProgress:
        """Update task progress and save to file.

        Args:
            task: Task to update
            execution_start: Optional start time

        Returns:
            Updated TaskProgress
        """
        # Get current progress
        progress = self.get_progress(task)

        # Update metadata
        self.update_task_metadata(task, progress, execution_start)

        # Save to file
        self.save_task(task)

        logger.info(
            f"Updated and saved task {task.id}: {progress.progress_string} ({progress.completion_percentage}%)"
        )
        return progress

    def generate_progress_report(
        self, tasks: list[Task], session_start: datetime, session_end: datetime
    ) -> str:
        """Generate progress report for multiple tasks.

        Args:
            tasks: List of tasks to report on
            session_start: Session start time
            session_end: Session end time

        Returns:
            Markdown-formatted progress report
        """
        duration = session_end - session_start
        duration_str = f"{int(duration.total_seconds() / 60)}m"

        report = [
            "# Task Loop Progress Report\n",
            f"**Session**: {session_start.strftime('%Y-%m-%d %H:%M')}-{session_end.strftime('%H:%M')}",
            f"**Duration**: {duration_str}",
            f"**Tasks**: {len(tasks)}\n",
        ]

        # Group by state
        completed = [t for t in tasks if t.state == "done"]
        in_progress = [t for t in tasks if t.state == "active"]
        blocked = [t for t in tasks if t.metadata.get("blockers")]

        if completed:
            report.append("## Completed Tasks\n")
            for task in completed:
                progress = self.get_progress(task)
                report.append(
                    f"- **{task.id}**: {task.title} ({progress.progress_string}) ✓"
                )
            report.append("")

        if in_progress:
            report.append("## In Progress\n")
            for task in in_progress:
                progress = self.get_progress(task)
                report.append(
                    f"- **{task.id}**: {task.title} ({progress.progress_string}, {progress.completion_percentage}%)"
                )
            report.append("")

        if blocked:
            report.append("## Blocked\n")
            for task in blocked:
                blockers = task.metadata.get("blockers", [])
                report.append(f"- **{task.id}**: {task.title}")
                for blocker in blockers:
                    report.append(f"  - {blocker}")
            report.append("")

        return "\n".join(report)
