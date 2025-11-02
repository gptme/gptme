"""Task loading and management for task loop mode."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Represents a task with metadata and content."""

    id: str
    title: str
    content: str
    state: str = "new"
    priority: str = "medium"
    tags: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    file_path: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> "Task":
        """Load task from markdown file with YAML frontmatter."""
        content = path.read_text()

        # Parse YAML frontmatter
        if content.startswith("---\n"):
            parts = content.split("---\n", 2)
            if len(parts) >= 3:
                try:
                    metadata = yaml.safe_load(parts[1]) or {}
                    body = parts[2].strip()
                except yaml.YAMLError as e:
                    logger.warning(f"Failed to parse frontmatter in {path}: {e}")
                    metadata = {}
                    body = content
            else:
                metadata = {}
                body = content
        else:
            metadata = {}
            body = content

        # Extract title from first heading or filename
        lines = body.split("\n")
        title = None
        for line in lines:
            if line.startswith("# "):
                title = line[2:].strip()
                break
        if not title:
            title = path.stem.replace("-", " ").title()

        return cls(
            id=path.stem,
            title=title,
            content=body,
            state=metadata.get("state", "new"),
            priority=metadata.get("priority", "medium"),
            tags=metadata.get("tags", []),
            depends=metadata.get("depends", []),
            metadata=metadata,
            file_path=path,
        )

    def __str__(self) -> str:
        """String representation of task."""
        return f"Task({self.id}, state={self.state}, priority={self.priority})"


class TaskLoader:
    """Loads and manages tasks from filesystem."""

    def __init__(self, tasks_dir: Path | str):
        """Initialize task loader with tasks directory."""
        self.tasks_dir = Path(tasks_dir)
        if not self.tasks_dir.exists():
            raise ValueError(f"Tasks directory not found: {self.tasks_dir}")

        self._tasks: dict[str, Task] = {}
        logger.info(f"TaskLoader initialized with directory: {self.tasks_dir}")

    def load_all(self) -> dict[str, Task]:
        """Load all tasks from directory."""
        self._tasks = {}

        for path in self.tasks_dir.glob("*.md"):
            try:
                task = Task.from_file(path)
                self._tasks[task.id] = task
                logger.debug(f"Loaded task: {task}")
            except Exception as e:
                logger.error(f"Failed to load task from {path}: {e}")

        logger.info(f"Loaded {len(self._tasks)} tasks")
        return self._tasks

    def get_task(self, task_id: str) -> Task | None:
        """Get specific task by ID."""
        return self._tasks.get(task_id)

    def filter_by_state(self, state: str) -> list[Task]:
        """Get all tasks with given state."""
        return [t for t in self._tasks.values() if t.state == state]

    def filter_by_priority(self, priority: str) -> list[Task]:
        """Get all tasks with given priority."""
        return [t for t in self._tasks.values() if t.priority == priority]

    def filter_by_tag(self, tag: str) -> list[Task]:
        """Get all tasks with given tag."""
        return [t for t in self._tasks.values() if tag in t.tags]

    def get_actionable_tasks(self) -> list[Task]:
        """Get tasks that are ready to be worked on (no unsatisfied dependencies)."""
        actionable = []

        for task in self._tasks.values():
            # Only consider new or active tasks
            if task.state not in ["new", "active"]:
                continue

            # Check if dependencies are satisfied
            deps_satisfied = True
            for dep_id in task.depends:
                dep_task = self.get_task(dep_id)
                if dep_task and dep_task.state != "done":
                    deps_satisfied = False
                    break

            if deps_satisfied:
                actionable.append(task)

        return actionable

    def select_next_task(self) -> Task | None:
        """Select next task to work on based on priority and dependencies."""
        actionable = self.get_actionable_tasks()

        if not actionable:
            return None

        # Sort by priority (high > medium > low)
        priority_order = {"high": 3, "medium": 2, "low": 1}
        actionable.sort(
            key=lambda t: (priority_order.get(t.priority, 0), t.id), reverse=True
        )

        return actionable[0]
