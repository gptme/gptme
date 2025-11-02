#!/usr/bin/env python3
"""
Loop Mode Execution Example

This example demonstrates executing multiple tasks in sequence
with progress tracking.
"""

from gptme.tasks import TaskExecutor

# 1. Configure execution
tasks_dir = "/home/bob/gptme-bob/tasks"
executor = TaskExecutor(tasks_dir=tasks_dir)

# 2. Execute multiple tasks in loop
print("Starting task loop...")
print("Config: max_tasks=3, max_time=900s (15 min)\n")

results = executor.run_loop(max_tasks=3, max_time_seconds=900)

# 3. Summarize results
print(f"\n{'='*60}")
print("Execution Summary")
print(f"{'='*60}")

total_time = results.get("total_time", 0)
tasks_completed = results.get("tasks_completed", 0)
tasks_failed = results.get("tasks_failed", 0)

print(f"Total tasks: {tasks_completed + tasks_failed}")
print(f"Successful: {tasks_completed}")
print(f"Failed: {tasks_failed}")
print(f"Total time: {total_time:.1f}s")

if tasks_completed + tasks_failed > 0:
    avg_time = total_time / (tasks_completed + tasks_failed)
    print(f"Average time per task: {avg_time:.1f}s")

# Display individual task results if available
task_results = results.get("task_results", {})
if task_results:
    print(f"\n{'='*60}")
    print("Individual Task Results")
    print(f"{'='*60}")
    for task_id, task_result in task_results.items():
        status = "✓" if task_result.get("success") else "✗"
        duration = task_result.get("duration", 0)
        print(f"{status} {task_id}: {duration:.1f}s")
        if not task_result.get("success"):
            error = task_result.get("error", "Unknown error")
            print(f"   Error: {error}")
