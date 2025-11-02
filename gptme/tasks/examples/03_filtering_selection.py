#!/usr/bin/env python3
"""
Task Filtering and Selection Example

This example demonstrates advanced task filtering and intelligent
selection based on various criteria.
"""

from gptme.tasks import TaskLoader

# 1. Initialize loader
tasks_dir = "/home/bob/gptme-bob/tasks"
loader = TaskLoader(tasks_dir=tasks_dir)

# 2. Load all tasks
all_tasks = loader.load_all()
print(f"Total tasks in workspace: {len(all_tasks)}\n")

# 3. Filter by state
new_tasks = [t for t in all_tasks.values() if t.state == "new"]
active_tasks = [t for t in all_tasks.values() if t.state == "active"]
print(f"New tasks: {len(new_tasks)}")
print(f"Active tasks: {len(active_tasks)}\n")

# 4. Filter by priority
high_priority = [t for t in all_tasks.values() if t.priority == "high"]
print(f"High priority tasks: {len(high_priority)}")
for task in list(high_priority)[:3]:
    print(f"  - {task.id} ({task.state})")
print()

# 5. Filter by context tags
autonomous_tasks = [t for t in all_tasks.values() if "@autonomous" in t.tags]
coding_tasks = [t for t in all_tasks.values() if "@coding" in t.tags]
print(f"Autonomous tasks: {len(autonomous_tasks)}")
print(f"Coding tasks: {len(coding_tasks)}\n")

# 6. Filter by dependencies
tasks_with_deps = [t for t in all_tasks.values() if t.depends]
print(f"Tasks with dependencies: {len(tasks_with_deps)}")
for task in list(tasks_with_deps)[:3]:
    print(f"  - {task.id} depends on: {', '.join(task.depends)}")
print()

# 7. Find blocked tasks (check metadata for waiting_for)
blocked_tasks = [t for t in all_tasks.values() if t.metadata.get("waiting_for")]
print(f"Blocked tasks: {len(blocked_tasks)}")
for task in list(blocked_tasks)[:3]:
    waiting_for = task.metadata.get("waiting_for", "Unknown")
    print(f"  - {task.id} waiting for: {waiting_for}")
print()

# 8. Intelligent selection (MIQ-based)
print("Selecting next task using MIQ scoring...")
next_task = loader.select_next_task()

if next_task:
    print(f"\nSelected: {next_task.id}")
    print(f"  Priority: {next_task.priority}")
    print(f"  State: {next_task.state}")
    print(f"  Tags: {', '.join(next_task.tags)}")
    # next_action is in metadata, not direct attribute
    next_action = next_task.metadata.get("next_action")
    if next_action:
        print(f"  Next action: {next_action}")
else:
    print("\nNo tasks available to execute")
