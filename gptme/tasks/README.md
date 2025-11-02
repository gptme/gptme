# Task Automation System

The task automation system enables gptme to autonomously execute tasks from a workspace, tracking progress and managing dependencies.

## Overview

This system provides:
- **Task Loading**: Discover and parse tasks from a workspace directory
- **Task Selection**: Intelligently select next task based on MIQ scoring and dependencies
- **Task Execution**: Run gptme sessions to complete tasks with subprocess-based execution
- **Progress Tracking**: Monitor completion rates and update task metadata
- **Conversation Management**: Integrate with gptme's conversation system for state tracking

## Components

### TaskLoader

Loads tasks from a workspace directory and selects the next task to execute.

```python
from gptme.tasks import TaskLoader

loader = TaskLoader(tasks_dir="/path/to/workspace/tasks")
tasks = loader.load_all()
next_task = loader.select_next_task()
```

**Features**:
- Parses YAML frontmatter from task markdown files
- Filters by state, priority, tags, and context
- Resolves task dependencies
- MIQ-based scoring for intelligent selection

**Task States**:
- `new`: Newly created, not yet started
- `active`: Currently being worked on
- `paused`: Temporarily halted
- `done`: Completed successfully
- `cancelled`: Abandoned
- `someday`: Deferred for future consideration

### TaskExecutor

Executes tasks by running gptme sessions in separate processes.

```python
from gptme.tasks import TaskExecutor

executor = TaskExecutor(tasks_dir="/path/to/workspace/tasks")

# Execute a specific task
task = executor.select_next_task()
result = executor.execute_task(task)

# Or run in loop mode
results = executor.run_loop(max_tasks=5, max_time_seconds=1500)
```

**Features**:
- Subprocess-based execution for isolation
- Configurable timeouts per task
- Automatic state updates (new → active → done)
- Error handling and recovery
- Results returned as ExecutionResult objects

**Execution Result** (dict):
```python
{
    "success": bool,         # Whether task completed successfully
    "output": str,           # Standard output from execution
    "error": str,            # Error message if failed
    "duration": float,       # Execution time in seconds
}
```

### TaskPlanner

Plans execution strategy based on task complexity and requirements.

```python
from gptme.tasks.planner import TaskPlanner

planner = TaskPlanner()
plan = planner.plan_execution(task)
```

**Features**:
- MIQ-based strategy determination (incremental, deep-focus, research-heavy)
- Phase breakdown based on task type and complexity
- Quality validation framework
- Resource requirement estimation

**ExecutionPlan**:
```python
@dataclass
class ExecutionPlan:
    strategy: str  # incremental, deep-focus, research-heavy
    phases: List[str]
    estimated_sessions: int
    quality_checks: List[str]
```

### TaskProgressTracker

Tracks progress during execution and updates conversation state.

```python
from gptme.tasks import TaskProgressTracker

tracker = TaskProgressTracker(tasks_dir="/path/to/workspace/tasks")

# Parse progress from task content
completed, total = tracker.parse_subtasks(task.content)
percentage = (completed / total) * 100 if total > 0 else 0
```

**Features**:
- Real-time progress monitoring during execution
- Percentage calculations based on subtask completion
- Conversation integration for state persistence
- Automatic task metadata updates

## CLI Integration

Task automation is available via the `--task-loop` flag:

```bash
# Execute a single task
gptme --task-loop --workspace /path/to/workspace

# Run multiple tasks with timeout
gptme --task-loop --max-tasks 5 --timeout 300 --workspace /path/to/workspace
```

**CLI Flags**:
- `--task-loop`: Enable task loop mode
- `--max-tasks N`: Maximum number of tasks to execute (default: 1)
- `--timeout N`: Timeout in seconds per task (default: 300)
- `--workspace PATH`: Path to workspace directory

## Task Metadata Format

Tasks are markdown files with YAML frontmatter:

```yaml
---
state: new
created: 2025-11-02
priority: high
task_type: project
tags: [feature, automation]
depends: [other-task-id]
next_action: "Write initial implementation"
---

# Task Title

Task description and details...

## Subtasks
- [ ] First subtask
- [x] Completed subtask
- [ ] Another subtask
```

**Required Fields**:
- `state`: Task state (new, active, paused, done, cancelled, someday)
- `created`: Creation date (ISO 8601)

**Optional Fields**:
- `priority`: Priority level (low, medium, high)
- `task_type`: Type (project or action)
- `tags`: List of tags for filtering
- `depends`: List of task IDs this depends on
- `next_action`: Clear next step (GTD principle)
- `waiting_for`: What/who task is waiting on
- `waiting_since`: When waiting started

## Usage Examples

### Basic Task Execution

```python
from gptme.tasks import TaskLoader, TaskExecutor

# Load and select task
loader = TaskLoader(workspace="/home/bob/gptme-bob")
next_task = loader.select_next_task()

# Execute task
executor = TaskExecutor(workspace="/home/bob/gptme-bob")
result = executor.execute_task(next_task.id, timeout=600)

if result.success:
    print(f"Task completed in {result.duration:.1f}s")
else:
    print(f"Task failed: {result.error}")
```

### Loop Mode with Filtering

```python
# Execute multiple autonomous tasks
executor = TaskExecutor(workspace="/home/bob/gptme-bob")
results = executor.run_loop(
    max_tasks=3,
    timeout_per_task=300,
    filter_tags=["@autonomous"],
    filter_priority="high"
)

for task_id, result in results.items():
    print(f"{task_id}: {'✓' if result.success else '✗'}")
```

### Dependency Resolution

```python
# TaskLoader automatically resolves dependencies
loader = TaskLoader(workspace="/home/bob/gptme-bob")

# Select task (skips tasks with unmet dependencies)
next_task = loader.select_next_task()

# Check dependencies
if next_task.depends:
    print(f"Dependencies: {', '.join(next_task.depends)}")
```

### Progress Tracking

```python
from gptme.tasks import TaskProgressTracker
from gptme.logmanager import LogManager

# Track progress during execution
log_manager = LogManager.load("conversation_name")
tracker = TaskProgressTracker()

progress = tracker.track_task("my-task", log_manager)
print(f"Progress: {progress.percentage}%")
print(f"Completed: {progress.completed}/{progress.total}")
```

## Best Practices

### Task Organization

1. **Use Clear Titles**: Descriptive task names aid selection
2. **Set next_action**: Define immediate next step (GTD principle)
3. **Tag Appropriately**: Use context tags (@autonomous, @coding, @research)
4. **Document Dependencies**: Link related tasks with `depends`

### Execution Strategy

1. **Start Small**: Begin with `max_tasks=1` to validate workflow
2. **Set Realistic Timeouts**: Complex tasks may need 600+ seconds
3. **Monitor Progress**: Check logs in `workspace/logs/` directory
4. **Handle Failures**: Tasks automatically return to previous state on error

### Context Tags

Use context tags for efficient filtering:
- `@autonomous`: Fully automatable without human input
- `@coding`: Writing/editing code
- `@research`: Web search, reading
- `@review`: PR review, code review
- `@terminal`: Terminal-based work

### MIQ Scoring

Tasks are scored on 5 dimensions (0.0-1.0 each):
1. **Momentum**: Continuation vs. context switching
2. **Impact**: Strategic value and effect size
3. **Quality**: Verification and objective criteria
4. **Urgency**: Time sensitivity
5. **Dependencies**: Blocking other work

**Total Score**: Average of all dimensions (0.0-1.0)

Higher scores = higher priority for autonomous execution.

## Troubleshooting

### Task Not Selected

**Symptoms**: `select_next_task()` returns None

**Causes**:
- All tasks in wrong state (need `state: new` or `state: active`)
- Dependencies not met (check `depends` field)
- Filters too restrictive (tags, priority)
- `waiting_for` field set (task blocked)

**Solutions**:
```bash
# Check task states
./scripts/tasks.py status --compact

# List tasks with specific context
./scripts/tasks.py list --context @autonomous

# Clear waiting_for if resolved
./scripts/tasks.py edit my-task --set waiting_for none
```

### Execution Timeout

**Symptoms**: Task fails with timeout error

**Causes**:
- Task too complex for time limit
- Blocked on external resource
- Infinite loop in task logic

**Solutions**:
- Increase timeout: `--timeout 600`
- Break task into smaller subtasks
- Check task logs in `workspace/logs/`

### Progress Not Updating

**Symptoms**: Percentage stays at 0% despite work

**Causes**:
- Subtasks not formatted correctly (need `- [ ]` checkboxes)
- Task file not saved/committed
- Tracker not integrated with conversation

**Solutions**:
```markdown
# Correct subtask format
## Subtasks
- [ ] Uncompleted subtask
- [x] Completed subtask
```

### Dependency Cycle

**Symptoms**: No tasks selected despite having `new` tasks

**Causes**:
- Task A depends on B, B depends on A (circular)
- Chain of dependencies forms cycle

**Solutions**:
- Check dependency graph: `./scripts/tasks.py show task-id`
- Break circular dependencies
- Use `next_action` instead of `depends` for sequencing

## Integration with gptme

### Conversation State

Task progress is tracked in conversation state:

```python
# In gptme session
task_results = {
    "task-id": {"success": True, "duration": 45.2},
    "other-task": {"success": False, "error": "Timeout"}
}
log_manager.append(Message("system", f"Task results: {task_results}"))
```

### Autonomous Runs

Task loop mode integrates with autonomous operation:

```bash
# In autonomous-run.sh
if [ "$TASK_LOOP_MODE" = "true" ]; then
    gptme --task-loop --max-tasks 1 --workspace /home/bob/gptme-bob
fi
```

### Pre-commit Integration

Tasks are validated automatically:

```bash
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: validate-tasks
      name: Validate task metadata
      entry: ./scripts/tasks/validate.py
      language: python
```

## Architecture

### Execution Flow
