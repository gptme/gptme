"""
A working memory todo tool for conversation-scoped task planning.

This tool provides a lightweight todo list that exists within the current conversation context,
complementing the existing persistent task management system in gptme-agent-template.

Key principles:

- Working Memory Layer: Ephemeral todos for current conversation context
- Complements Persistent Tasks: Works alongside existing task files without conflicts
- Simple State Model: pending, in_progress, completed, paused
- Conversation Scoped: Resets between conversations, doesn't persist to disk
- Auto-replay: Automatically restores todo state when resuming conversations
"""

import logging
import shlex
from collections import Counter
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from dateutil.parser import isoparse

from ..hooks import HookType
from ..hooks.types import ToolExecutePreData
from ..logmanager import LogManager
from ..message import Message
from .base import Parameter, ToolSpec, ToolUse

logger = logging.getLogger(__name__)

# Server workers recreate LogManagers between tools, so ownership is the log
# directory and branch, not a manager instance or an inherited ContextVar value.
_todo_stores: dict[tuple[Path, str], dict[str, dict]] = {}
_todo_lock = RLock()


def _get_todos() -> dict[str, dict]:
    """Return the current conversation's store; unbound readers see no todos."""
    manager = LogManager.get_current_log()
    if manager is None:
        return {}
    with _todo_lock:
        return _todo_stores.setdefault(
            (manager.logdir.resolve(), manager.current_branch), {}
        )


def _snapshot_todos() -> dict[str, dict]:
    manager = LogManager.get_current_log()
    if manager is None:
        return {}
    with _todo_lock:
        todos = _todo_stores.get((manager.logdir.resolve(), manager.current_branch), {})
        return {key: value.copy() for key, value in todos.items()}


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
        self.created = created or datetime.now(tz=timezone.utc)
        self.updated = datetime.now(tz=timezone.utc)

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


def _generate_todo_id(todos: dict[str, dict]) -> str:
    """Generate a simple incremental ID for new todos."""
    existing_ids = [int(id) for id in todos if id.isdigit()]
    return str(max(existing_ids, default=0) + 1)


def has_incomplete_todos() -> bool:
    """Check if there are any incomplete todos in working memory.

    Used by auto_reply_hook to determine if the agent should continue
    working instead of being asked about completion.

    Returns:
        True if there are any pending or in_progress todos.
    """
    for todo in _snapshot_todos().values():
        if todo["state"] in ("pending", "in_progress", "paused"):
            return True
    return False


def get_incomplete_todos_summary() -> str:
    """Get a summary of incomplete todos for continuation prompts.

    Returns:
        A formatted string listing incomplete todos, or empty string if none.
    """
    incomplete = [
        todo
        for todo in _snapshot_todos().values()
        if todo["state"] in ("pending", "in_progress", "paused")
    ]
    if not incomplete:
        return ""

    # Sort by ID for consistent ordering (numeric IDs first, then alphabetically)
    incomplete.sort(
        key=lambda x: (0, int(x["id"])) if x["id"].isdigit() else (1, x["id"])
    )

    lines = []
    for todo in incomplete:
        state_emoji = {"in_progress": "🔄", "paused": "⏸️"}.get(todo["state"], "🔲")
        lines.append(f"  {state_emoji} {todo['text']}")

    return "\n".join(lines)


def _format_todo_list() -> str:
    """Format the current todo list for display."""
    todos = _snapshot_todos()
    if not todos:
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
    all_todos = sorted(todos.values(), key=lambda x: int(x["id"]))

    for todo in all_todos:
        emoji = state_emojis[todo["state"]]
        output.append(f"{todo['id']}. {emoji} {todo['text']}")

    # Summary
    total = len(todos)
    state_counter = Counter(t["state"] for t in todos.values())
    breakdown = ", ".join([f"{k}: {v}" for k, v in state_counter.items()])

    output.append("")
    output.append(f"Summary: {total} total ({breakdown})")

    return "\n".join(output)


def _write_operations(tooluse: ToolUse) -> list[str]:
    """Return the write operations of a todo call, in any invocation format.

    Handles the positional form (``@todo write`` plus a body) and the
    single-string form that native tool calls use (``{"command": "write add
    ..."}``), including a write operation sent without the ``write``
    subcommand.
    """
    if tooluse.args:
        if tooluse.args[0].lower() != "write":
            return []
        body = tooluse.content or ""
    else:
        command = (tooluse.kwargs or {}).get("command", "")
        if not isinstance(command, str):
            # A malformed call (e.g. {"command": null}) is skipped, not fatal.
            return []
        subcommand, body = _split_command(command)
        if subcommand != "write":
            if subcommand not in _WRITE_OPS:
                return []
            body = command
    return [line.strip() for line in body.strip().split("\n") if line.strip()]


def _replay_lines(tooluse: ToolUse) -> list[str]:
    """Operation lines to replay for a todo call (any invocation format)."""
    return _write_operations(tooluse)


def _call_identity(call: ToolUse) -> tuple:
    return call.tool, call.args, call.content, call.kwargs, call.call_id


def _restore_todos(
    messages: list[Message], stop_before: ToolUse | None = None
) -> tuple[dict[str, dict], bool]:
    """Rebuild into a private store so replay cannot touch another conversation."""
    todos: dict[str, dict] = {}
    restored = False
    for msg in messages:
        # System prompts contain examples, not executed assistant instructions.
        if msg.role != "assistant":
            continue
        for tooluse in ToolUse.iter_from_content(msg.content):
            if (
                stop_before is not None
                and msg is messages[-1]
                and _call_identity(tooluse) == _call_identity(stop_before)
                and (stop_before.start is None or tooluse.start == stop_before.start)
            ):
                return todos, restored
            if tooluse.tool != "todo":
                continue
            for line in _write_operations(tooluse):
                try:
                    parts = shlex.split(line)
                    if parts:
                        _apply_write(todos, parts[0], *parts[1:])
                        restored = True
                except Exception as exc:
                    logger.warning("Failed to replay todo operation %r: %s", line, exc)
                    # Execution aborts the rest of this call on a parse error.
                    # Later calls remain independent and can still be replayed.
                    break
    return todos, restored


def replay_todo_on_session_start(
    logdir: Path | None,
    workspace: Path | None,
    initial_msgs: list[Message],
    branch: str | None = None,
    **kwargs: object,
) -> Generator[Message, None, None]:
    """Restore only the named conversation, even before its manager is loaded."""
    if logdir is None:
        return
    path = Path(logdir).resolve()
    manager = LogManager.get_current_log()
    if branch is None:
        branch = (
            manager.current_branch
            if manager is not None and manager.logdir.resolve() == path
            else "main"
        )
    with _todo_lock:
        todos, restored = _restore_todos(initial_msgs)
        if restored:
            _todo_stores[path, branch] = todos
        else:
            # CLI initial_msgs may contain only prompts; load() reads the saved
            # history afterwards. Leave restoration to STEP_PRE in that case.
            _todo_stores.pop((path, branch), None)
    if restored:
        yield Message("system", "Restored todo state from previous session", hide=True)


def ensure_todo_state(
    manager: LogManager, **kwargs: object
) -> Generator[Message, None, None]:
    """Restore resumed server conversations before generating any new tool calls.

    Server SESSION_START runs only for new conversations. Never lazily replay
    from a tool getter: the log may already contain the call being executed.
    """
    key = (manager.logdir.resolve(), manager.current_branch)
    with _todo_lock:
        if key not in _todo_stores:
            log = (
                manager.master_log if manager.current_branch == "main" else manager.log
            )
            _todo_stores[key], _ = _restore_todos(log.messages)
    yield from ()


def restore_todos_before_tool(
    data: ToolExecutePreData,
) -> Generator[Message, None, None]:
    """Restore direct/rerun calls without replaying their pending message."""
    manager = LogManager.get_current_log()
    if manager is None or data.tool_use is None or data.tool_use.tool != "todo":
        return
    key = (manager.logdir.resolve(), manager.current_branch)
    with _todo_lock:
        if key in _todo_stores:
            return
        log = manager.master_log if manager.current_branch == "main" else manager.log
        messages = log.messages

        # The assistant message is already persisted by the server tool worker.
        # Keep calls before this one (which may have executed before a restart),
        # but exclude this call and later calls that have not executed yet.
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "assistant":
                messages = messages[: index + 1]
                break
        _todo_stores[key], _ = _restore_todos(messages, stop_before=data.tool_use)
    yield from ()


def clear_conversation_todos(logdir: Path) -> None:
    """Release stores after the owner has established the conversation is idle."""
    path = logdir.resolve()
    with _todo_lock:
        for key in list(_todo_stores):
            if key[0] == path:
                del _todo_stores[key]


def clear_todo_state(
    manager: LogManager, server_managed: bool = False, **kwargs: object
) -> Generator[Message, None, None]:
    """Release CLI stores; server eviction must synchronize with reconnects."""
    if not server_managed:
        clear_conversation_todos(manager.logdir)
    yield from ()


def _todo(operation: str, *args: str) -> str:
    """Helper function for todo replay - routes to appropriate handler.

    This exists for compatibility with _replay_tool which looks for _{tool_name} helpers.
    """
    operation = operation.lower()
    if operation == "read":
        return _todoread()
    # Treat as a write operation (add, update, remove, clear)
    return _todowrite(operation, *args)


def _todoread() -> str:
    """Helper function for todo read - used by tests and execute function."""
    return _format_todo_list()


def _todowrite(operation: str, *args: str) -> str:
    """Helper function for todo write - used by tests and execute function."""
    if LogManager.get_current_log() is None:
        raise RuntimeError("Todo writes require an active conversation")
    with _todo_lock:
        return _apply_write(_get_todos(), operation, *args)


def _apply_write(todos: dict[str, dict], operation: str, *args: str) -> str:
    """Apply one operation to an explicitly owned store (also used by replay)."""
    operation = operation.lower()

    if operation == "add":
        if not args:
            return 'Error: add requires todo text. Usage: add "todo text"'

        todo_text = " ".join(args).strip("\"'")
        todo_id = _generate_todo_id(todos)

        item = TodoItem(todo_id, todo_text)
        todos[todo_id] = item.to_dict()

        return f"Added todo {todo_id}: {todo_text}"

    if operation == "update":
        if len(args) < 2:
            return 'Error: update requires ID and state/text. Usage: update ID state OR update ID "new text"'

        todo_id = args[0]
        if todo_id not in todos:
            return f"Error: Todo {todo_id} not found"

        update_value = " ".join(args[1:]).strip("\"'")

        # Check if it's a state update or text update
        valid_states = ["pending", "in_progress", "completed", "paused"]
        if update_value in valid_states:
            todos[todo_id]["state"] = update_value
            todos[todo_id]["updated"] = datetime.now(tz=timezone.utc).isoformat()
            return f"Updated todo {todo_id} state to: {update_value}"
        todos[todo_id]["text"] = update_value
        todos[todo_id]["updated"] = datetime.now(tz=timezone.utc).isoformat()
        return f"Updated todo {todo_id} text to: {update_value}"

    if operation == "remove":
        if not args:
            return "Error: remove requires ID. Usage: remove ID"

        todo_id = args[0]
        if todo_id not in todos:
            return f"Error: Todo {todo_id} not found"

        todo_text = todos[todo_id]["text"]
        del todos[todo_id]
        return f"Removed todo {todo_id}: {todo_text}"

    if operation == "clear":
        if args and args[0].lower() == "completed":
            # Clear only completed todos
            completed_ids = [
                id for id, todo in todos.items() if todo["state"] == "completed"
            ]
            for todo_id in completed_ids:
                del todos[todo_id]
            count = len(completed_ids)
            return f"Cleared {count} completed todos"
        # Clear all todos
        count = len(todos)
        todos.clear()
        return f"Cleared {count} todos"

    return f"Error: Unknown operation '{operation}'. Use: add, update, remove, clear"


# Write operations accepted as a bare first token, e.g. a caller that sends
# 'add "text"' without the 'write' subcommand.
_WRITE_OPS = ("add", "update", "remove", "clear")


def _split_command(command: str) -> tuple[str, str]:
    """Split a single-command string into (subcommand, body).

    Structured ("tool"-format) callers supply the whole command as one string,
    e.g. ``write add "do the thing"``. The subcommand is the first token, the
    rest is the operation body.
    """
    parts = command.strip().split(None, 1)
    if not parts:
        return "", ""
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""


def execute_todo(
    code: str | None,
    args: list[str] | None,
    kwargs: dict[str, str] | None,
) -> Generator[Message, None, None]:
    """Execute todo command with read/write subcommands."""
    if not args:
        # Structured ("tool"-format) callers supply the whole command as a
        # single ``command`` kwarg and leave ``args`` empty. Recover the
        # subcommand and the body from that string so both invocation styles
        # work (markdown blocks set ``args`` positionally).
        command = (kwargs or {}).get("command", "")
        if not isinstance(command, str):
            command = ""
        command = command.strip()
        if command:
            subcommand, body = _split_command(command)
            if subcommand in _WRITE_OPS:
                # A write operation sent without the 'write' subcommand.
                subcommand, body = "write", command.strip()
            args = [subcommand]
            code = body

    if not args:
        # Default to read if no subcommand
        yield Message("system", _todoread())
        return

    subcommand = args[0].lower()

    if subcommand == "read":
        yield Message("system", _todoread())
        return

    elif subcommand == "write":
        if not code:
            yield Message(
                "system",
                'Error: todo write requires operations. Usage: add "todo text" | update ID state | remove ID | clear',
            )
            return

        # Split code into lines for multiple operations
        lines = [line.strip() for line in code.strip().split("\n") if line.strip()]

        if not lines:
            yield Message(
                "system",
                'Error: todo write requires operations. Usage: add "todo text" | update ID state | remove ID | clear',
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

    else:
        yield Message(
            "system",
            f"Error: Unknown subcommand '{subcommand}'. Use: todo read | todo write",
        )


def examples_todo(tool_format):
    """Generate examples for todo tool."""
    return f"""
> User: What's on my todo list?
> Assistant: Let me check the current todo list.
{ToolUse("todo", ["read"], "").to_output(tool_format)}
> System: Todo List:
...

> Assistant: I'll break this complex task into steps.
{
        ToolUse(
            "todo",
            ["write"],
            '''
add "Set up project structure"
add "Implement core functionality"
'''.strip(),
        ).to_output(tool_format)
    }

> Assistant: Starting the first task.
{ToolUse("todo", ["write"], "update 1 in_progress").to_output(tool_format)}

> Assistant: Completed the project setup.
{
        ToolUse(
            "todo",
            ["write"],
            '''
update 1 completed
update 2 in_progress
'''.strip(),
        ).to_output(tool_format)
    }

> Assistant: Clearing completed todos to focus on remaining work.
{ToolUse("todo", ["write"], "clear completed").to_output(tool_format)}
""".strip()


# Tool specification
todo = ToolSpec(
    name="todo",
    desc="Manage an in-session todo list (ephemeral, not persisted across conversations)",
    block_types=["todo"],
    parameters=[
        Parameter(
            name="command",
            type="string",
            required=False,
            description=(
                'Todo command, e.g. "read" or \'write add "todo text"\'. '
                "Multiple write operations can be separated by newlines."
            ),
        )
    ],
    instructions="""
### When to use the todo tool

Use todo as working memory for 3+ step tasks in the current conversation.
For persistent cross-session tracking, use `gptodo` or task files instead.

Manage todos for the current conversation.

Subcommands:
- `todo read` - Show the current todo list
- `todo write` - Edit todos

The `command` parameter takes the whole command, e.g. `read` or
`write add "todo text"` (multiple write operations separated by newlines).

Write operations:
- add "todo text" - Add an item
- update ID state - Set state (pending/in_progress/completed/paused)
- update ID "new text" - Rename an item
- remove ID - Remove an item
- clear - Clear all todos
- clear completed - Clear completed todos

States: pending, in_progress, completed, paused

Todo state is auto-replayed when resuming the conversation.
    """.strip(),
    examples=examples_todo,
    execute=execute_todo,
    hooks={
        "replay_todos": (
            HookType.SESSION_START.value,
            replay_todo_on_session_start,
            10,  # High priority: run early in session start
        ),
        "ensure_todos": (HookType.STEP_PRE.value, ensure_todo_state, 10),
        "restore_todos_before_tool": (
            HookType.TOOL_EXECUTE_PRE.value,
            restore_todos_before_tool,
            10,
        ),
        "clear_todos": (HookType.SESSION_END.value, clear_todo_state, 10),
    },
)


__all__ = [
    "todo",
    "has_incomplete_todos",
    "get_incomplete_todos_summary",
]
