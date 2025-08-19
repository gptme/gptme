"""
A working memory todo tool for conversation-scoped task planning.

This tool provides a lightweight todo list that exists within the current conversation context,
complementing the existing persistent task management system in gptme-agent-template.

Key principles:
- Working Memory Layer: Ephemeral todos for current conversation context
- Complements Persistent Tasks: Works alongside existing task files without conflicts
- Simple State Model: pending, in_progress, completed
- Conversation Scoped: Resets between conversations, doesn't persist to disk
"""

from collections.abc import Generator
from datetime import datetime
import shlex

from ..message import Message
from .base import ConfirmFunc, ToolSpec, ToolUse

# Conversation-scoped storage for the current todo list
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
        self.state = state  # pending, in_progress, completed
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
        item.created = datetime.fromisoformat(data["created"])
        item.updated = datetime.fromisoformat(data["updated"])
        return item


def _generate_todo_id() -> str:
    """Generate a simple incremental ID for new todos."""
    existing_ids = [int(id) for id in _current_todos.keys() if id.isdigit()]
    return str(max(existing_ids, default=0) + 1)


def _format_todo_list() -> str:
    """Format the current todo list for display."""
    if not _current_todos:
        return "📝 Todo list is empty"

    # Group todos by state
    states = {
        "pending": "🔲 Pending",
        "in_progress": "🔄 In Progress",
        "completed": "✅ Completed",
    }

    output = ["📝 Current Todo List:", ""]

    for state, label in states.items():
        items = [item for item in _current_todos.values() if item["state"] == state]
        if items:
            output.append(f"{label}:")
            for item in sorted(items, key=lambda x: x["created"]):
                output.append(f"  {item['id']}. {item['text']}")
            output.append("")

    # Summary
    total = len(_current_todos)
    completed = len([t for t in _current_todos.values() if t["state"] == "completed"])
    pending = len([t for t in _current_todos.values() if t["state"] == "pending"])
    in_progress = len(
        [t for t in _current_todos.values() if t["state"] == "in_progress"]
    )

    output.append(
        f"Summary: {total} total ({completed} completed, {in_progress} in progress, {pending} pending)"
    )

    return "\n".join(output)


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

        return f"✅ Added todo {todo_id}: {todo_text}"

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
            return f"✅ Updated todo {todo_id} state to: {update_value}"
        else:
            _current_todos[todo_id]["text"] = update_value
            _current_todos[todo_id]["updated"] = datetime.now().isoformat()
            return f"✅ Updated todo {todo_id} text to: {update_value}"

    elif operation == "remove":
        if not args:
            return "Error: remove requires ID. Usage: remove ID"

        todo_id = args[0]
        if todo_id not in _current_todos:
            return f"Error: Todo {todo_id} not found"

        todo_text = _current_todos[todo_id]["text"]
        del _current_todos[todo_id]
        return f"✅ Removed todo {todo_id}: {todo_text}"

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
            return f"✅ Cleared {count} completed todos"
        else:
            # Clear all todos
            count = len(_current_todos)
            _current_todos.clear()
            return f"✅ Cleared {count} todos"

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

    # Parse the operation from code content
    parts = shlex.split(code.strip())
    if not parts:
        yield Message(
            "system",
            'Error: todowrite requires an operation. Usage: add "todo text" | update ID state | remove ID | clear',
        )
        return

    operation = parts[0]
    operation_args = parts[1:]

    # Use the helper function
    result = _todowrite(operation, *operation_args)
    yield Message("system", result)


def examples_todoread(tool_format):
    """Generate examples for todoread tool."""
    return f"""
> User: What's on my todo list?
> Assistant: Let me check the current todo list.
{ToolUse("todoread", [], "").to_output(tool_format)}
> System: 📝 Current Todo List:
...

> User: I've been working on a complex task, can you show me progress?
> Assistant: I'll check the todo list to see our progress.
{ToolUse("todoread", [], "").to_output(tool_format)}
""".strip()


def examples_todowrite(tool_format):
    """Generate examples for todowrite tool."""
    return f"""
> Assistant: I'll break this complex task into steps.
{ToolUse("todowrite", [], 'add "Set up project structure"').to_output(tool_format)}
{ToolUse("todowrite", [], 'add "Implement core functionality"').to_output(tool_format)}

> Assistant: Starting the first task.
{ToolUse("todowrite", [], "update 1 in_progress").to_output(tool_format)}

> Assistant: Completed the project setup.
{ToolUse("todowrite", [], "update 1 completed").to_output(tool_format)}
{ToolUse("todowrite", [], "update 2 in_progress").to_output(tool_format)}

> Assistant: Clearing completed todos to focus on remaining work.
{ToolUse("todowrite", [], "clear completed").to_output(tool_format)}
""".strip()


# Tool specifications
todoread = ToolSpec(
    name="todoread",
    desc="Read and display the current todo list",
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
    """.strip(),
    examples=examples_todowrite,
    execute=execute_todowrite,
)


__all__ = ["todoread", "todowrite"]
