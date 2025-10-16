"""
A working memory todo tool for conversation-scoped task planning.

This tool provides a lightweight todo list that exists within the current conversation context,
complementing the existing persistent task management system in gptme-agent-template.

Key principles:
- Working Memory Layer: Ephemeral todos for current conversation context
- Complements Persistent Tasks: Works alongside existing task files without conflicts
- Simple State Model: pending, in_progress, completed
- Conversation Scoped: Resets between conversations, doesn't persist to disk
- Auto-replay: Automatically restores todo state when resuming conversations
"""

import logging
import shlex
from collections import Counter
from collections.abc import Generator
from datetime import datetime

from dateutil.parser import isoparse

from ..hooks import HookType
from ..logmanager import Log
from ..message import Message
from .base import ConfirmFunc, ToolSpec, ToolUse

logger = logging.getLogger(__name__)

# Conversation-scoped storage for the current todo list
# TODO: support persistence to support resuming conversations
_current_todos: dict[str, dict] = {}


class TodoItem:
    """Represents a single todo item with state and metadata."""

    def __init__(
        self,
        id: str,
        text: str,
        state: str = "pending",
        created: datetime | None = None,
    ):
        self.id = id
        self.text = text
        self.state = state  # pending, in_progress, completed, paused
        self.created = created or datetime.now()
        self.updated = datetime.now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "state": self.state,
            "created": self.created.isoformat(),
            "updated": self.updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TodoItem":
        item = cls(data["id"], data["text"], data["state"])
        item.created = isoparse(data["created"])
        item.updated = isoparse(data["updated"])
        return item


def _generate_todo_id() -> str:
    """Generate a simple incremental ID for new todos."""
    existing_ids = [int(id) for id in _current_todos.keys() if id.isdigit()]
    return str(max(existing_ids, default=0) + 1)


def _format_todo_list() -> str:
    """Format the current todo list for display."""
    if not _current_todos:
        return "📝 Todo list is empty"

    # State emojis
    state_emojis = {
        "pending": "🔲",
        "in_progress": "🔄",
        "completed": "✅",
        "paused": "⏸️",
    }

    output = ["Todo List:"]

    # Sort all todos by ID (task order)
    all_todos = sorted(_current_todos.values(), key=lambda x: int(x["id"]))

    for todo in all_todos:
        emoji = state_emojis[todo["state"]]
        output.append(f"{todo['id']}. {emoji} {todo['text']}")

    # Summary
    total = len(_current_todos)
    state_counter = Counter(t["state"] for t in _current_todos.values())
    breakdown = ", ".join([f"{k}: {v}" for k, v in state_counter.items()])

    output.append("")
    output.append(f"Summary: {total} total ({breakdown})")

    return "\n".join(output)


def replay_todowrite_on_session_start(
    logdir, workspace, initial_msgs, **kwargs
) -> Generator[Message, None, None]:
    """Hook function that replays todowrite operations at session start.

    This ensures todo state is restored when resuming a conversation.

    Args:
        logdir: Log directory path
        workspace: Workspace directory path
        initial_msgs: Initial messages in the log

    Yields:
        Messages about replay status (hidden)
    """
    if not initial_msgs:
        return

    # Check if there are any todowrite operations in the log
    has_todowrite = any(
        tooluse.tool == "todowrite"
        for msg in initial_msgs
        for tooluse in ToolUse.iter_from_content(msg.content)
    )

    if not has_todowrite:
        return

    logger.info("Detected todowrite operations, replaying to restore state...")

    try:
        # Import here to avoid circular dependency
        from ..commands import _replay_tool

        # Create a minimal Log object for replay
        log = Log(initial_msgs)

        # Replay todowrite operations
        _replay_tool(log, "todowrite")

        yield Message("system", "Restored todo state from previous session", hide=True)

    except Exception as e:
        logger.exception(f"Error replaying todowrite operations: {e}")
        yield Message(
            "system", f"Warning: Failed to restore todo state: {e}", hide=True
        )


def _todoread() -> str:
    """Helper function for todoread - used by tests and execute function."""
    return _format_todo_list()


def _todowrite(operation: str, *args: str) -> str:
    """Helper function for todowrite - used by tests and execute function."""
    operation = operation.lower()

    if operation == "add":
        if not args:
            return 'Error: add requires todo text. Usage: add "todo text"'

        todo_text = " ".join(args).strip("\"'")
        todo_id = _generate_todo_id()

        item = TodoItem(todo_id, todo_text)
        _current_todos[todo_id] = item.to_dict()

        return f"Added todo {todo_id}: {todo_text}"

    elif operation == "update":
        if len(args) < 2:
            return 'Error: update requires ID and state/text. Usage: update ID state OR update ID "new text"'

        todo_id = args[0]
        if todo_id not in _current_todos:
            return f"Error: Todo {todo_id} not found"

        update_value = " ".join(args[1:]).strip("\"'")

        # Check if it's a state update or text update
        valid_states = ["pending", "in_progress", "completed"]
        if update_value in valid_states:
            _current_todos[todo_id]["state"] = update_value
            _current_todos[todo_id]["updated"] = datetime.now().isoformat()
            return f"Updated todo {todo_id} state to: {update_value}"
        else:
            _current_todos[todo_id]["text"] = update_value
            _current_todos[todo_id]["updated"] = datetime.now().isoformat()
            return f"Updated todo {todo_id} text to: {update_value}"

    elif operation == "remove":
        if not args:
            return "Error: remove requires ID. Usage: remove ID"

        todo_id = args[0]
        if todo_id not in _current_todos:
            return f"Error: Todo {todo_id} not found"

        todo_text = _current_todos[todo_id]["text"]
        del _current_todos[todo_id]
        return f"Removed todo {todo_id}: {todo_text}"

    elif operation == "clear":
        if args and args[0].lower() == "completed":
            # Clear only completed todos
            completed_ids = [
                id
                for id, todo in _current_todos.items()
                if todo["state"] == "completed"
            ]
            for todo_id in completed_ids:
                del _current_todos[todo_id]
            count = len(completed_ids)
            return f"Cleared {count} completed todos"
        else:
            # Clear all todos
            count = len(_current_todos)
            _current_todos.clear()
            return f"Cleared {count} todos"

    else:
        return (
            f"Error: Unknown operation '{operation}'. Use: add, update, remove, clear"
        )


def execute_todoread(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute todoread command."""
    result = _todoread()
    yield Message("system", result)


def execute_todowrite(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
    confirm: ConfirmFunc,
) -> Generator[Message, None, None]:
    """Execute todowrite command."""
    if not code:
        yield Message(
            "system",
            'Error: todowrite requires an operation. Usage: add "todo text" | update ID state | remove ID | clear',
        )
        return

    # Split code into lines for multiple operations
    lines = [line.strip() for line in code.strip().split("\n") if line.strip()]

    if not lines:
        yield Message(
            "system",
            'Error: todowrite requires an operation. Usage: add "todo text" | update ID state | remove ID | clear',
        )
        return

    results = []

    # Process each line as a separate operation
    for line in lines:
        parts = shlex.split(line)
        if not parts:
            continue

        operation = parts[0]
        operation_args = parts[1:]

        # Use the helper function
        result = _todowrite(operation, *operation_args)
        results.append(result)

    # Combine results
    yield Message("system", "\n".join(results))


def examples_todoread(tool_format):
    """Generate examples for todoread tool."""
    return f"""
> User: What's on my todo list?
> Assistant: Let me check the current todo list.
{ToolUse("todoread", [], "").to_output(tool_format)}
> System: Todo List:
...

> User: I've been working on a complex task, can you show me progress?
> Assistant: I'll check the todo list to see our progress.
{ToolUse("todoread", [], "").to_output(tool_format)}
""".strip()


def examples_todowrite(tool_format):
    """Generate examples for todowrite tool."""
    return f"""
> Assistant: I'll break this complex task into steps.
{ToolUse("todowrite", [], '''
add "Set up project structure
add "Implement core functionality
'''.strip()).to_output(tool_format)}

> Assistant: Starting the first task.
{ToolUse("todowrite", [], "update 1 in_progress").to_output(tool_format)}

> Assistant: Completed the project setup.
{ToolUse("todowrite", [], '''
update 1 completed
update 2 in_progress
'''.strip()).to_output(tool_format)}

> Assistant: Clearing completed todos to focus on remaining work.
{ToolUse("todowrite", [], "clear completed").to_output(tool_format)}
""".strip()


# Tool specifications
todoread = ToolSpec(
    name="todoread",
    desc="Read and display the current todo list",
    block_types=["todoread"],
    instructions="""
Use this tool to read and display the current todo list.

This shows all todos organized by state (pending, in_progress, completed)
and provides a summary of progress.

Use this frequently to:
- Check current progress
- See what needs to be done next
- Review completed work
- Plan next steps

The todo list is conversation-scoped and resets between conversations.
For long-term persistent tasks, use the task management system instead.
    """.strip(),
    examples=examples_todoread,
    execute=execute_todoread,
)

todowrite = ToolSpec(
    name="todowrite",
    desc="Write, update, or manage todos in the current conversation",
    block_types=["todowrite"],
    instructions="""
Use this tool to manage todos in the current conversation context.

Operations:
- add "todo text" - Add a new todo item
- update ID state - Update todo state (pending/in_progress/completed)
- update ID "new text" - Update todo text
- remove ID - Remove a todo item
- clear - Clear all todos
- clear completed - Clear only completed todos

States: pending, in_progress, completed

Use this tool frequently for complex multi-step tasks to:
- Break down large tasks into smaller steps
- Track progress through complex workflows
- Provide visibility into your work plan
- Stay organized during long conversations

The todo list is ephemeral and conversation-scoped.
For persistent cross-conversation tasks, use the task management system.

Auto-replay: Todo operations are automatically replayed when resuming conversations
to restore your todo list state.
    """.strip(),
    examples=examples_todowrite,
    execute=execute_todowrite,
    hooks={
        "replay_todos": (
            HookType.SESSION_START.value,
            replay_todowrite_on_session_start,
            10,  # High priority: run early in session start
        )
    },
)


__all__ = ["todoread", "todowrite"]
