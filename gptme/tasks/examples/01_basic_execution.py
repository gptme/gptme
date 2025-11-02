#!/usr/bin/env python3
"""
Basic Task Execution Example

This example shows the simplest way to execute a single task.
"""

from gptme.tasks import TaskExecutor, TaskLoader

# 1. Initialize components with tasks directory
tasks_dir = "/home/bob/gptme-bob/tasks"
loader = TaskLoader(tasks_dir=tasks_dir)
executor = TaskExecutor(tasks_dir=tasks_dir)

# 2. Load and select next task
print("Loading tasks...")
tasks = executor.load_tasks()
print(f"Found {len(tasks)} tasks")

next_task = executor.select_next_task()
if not next_task:
    print("No tasks available to execute")
    exit(1)

print(f"Selected task: {next_task.id}")
print(f"Priority: {next_task.priority}")
print(f"State: {next_task.state}")

# 3. Execute the task
print(f"\nExecuting task '{next_task.id}'...")
result = executor.execute_task(next_task)

# 4. Check results
if result.get("success"):
    print("✓ Task completed successfully")
    print(f"Duration: {result.get('duration', 0):.1f}s")
    output = result.get("output", "")
    if output:
        print(f"Output preview: {output[:200]}...")
else:
    print("✗ Task failed")
    error = result.get("error", "Unknown error")
    print(f"Error: {error}")
    print(f"Duration: {result.get('duration', 0):.1f}s")
