#!/usr/bin/env python3
"""
Progress Tracking Example

This example demonstrates how to track task progress during execution
and integrate with conversation state.
"""

from gptme.tasks import TaskLoader, TaskProgressTracker

# 1. Initialize components
tasks_dir = "/home/bob/gptme-bob/tasks"
loader = TaskLoader(tasks_dir=tasks_dir)
tracker = TaskProgressTracker(tasks_dir=tasks_dir)

# 2. Select a task
next_task = loader.select_next_task()
if not next_task:
    print("No tasks available")
    exit(1)

print(f"Tracking progress for: {next_task.id}\n")

# 3. Parse initial progress from task content
completed, total = tracker.parse_subtasks(next_task.content)
if total > 0:
    percentage = (completed / total) * 100
    print("Initial Progress:")
    print(f"  Completed: {completed}/{total} subtasks")
    print(f"  Percentage: {percentage:.0f}%")
    print(f"  State: {next_task.state}\n")
else:
    print("No subtasks found in task content\n")

# 4. Task execution would happen here (via TaskExecutor)
print("In actual usage, task execution happens via TaskExecutor.execute_task()")
print("Progress is tracked automatically during execution")
print("See example 01 for basic execution\n")

# 5. Update progress after execution
# In real usage, this happens automatically during task execution
print("After execution, task state is updated:")
print("  - Subtasks checked off")
print("  - State changed (new → active → done)")
print("  - Metadata updated with completion time")
print("  - Progress tracked in conversation state")
