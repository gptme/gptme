"""Tests for the todo tools."""

import pytest

from gptme.tools.todo import TodoItem, _current_todos, _todoread, _todowrite


@pytest.fixture(autouse=True)
def clear_todos():
    """Clear todos before each test."""
    _current_todos.clear()
    yield
    _current_todos.clear()


def test_todo_item():
    """Test TodoItem class."""
    item = TodoItem("1", "Test todo", "pending")
    assert item.id == "1"
    assert item.text == "Test todo"
    assert item.state == "pending"

    # Test serialization
    data = item.to_dict()
    assert data["id"] == "1"
    assert data["text"] == "Test todo"
    assert data["state"] == "pending"
    assert "created" in data
    assert "updated" in data

    # Test deserialization
    item2 = TodoItem.from_dict(data)
    assert item2.id == item.id
    assert item2.text == item.text
    assert item2.state == item.state


def test_todoread_empty():
    """Test reading empty todo list."""
    result = _todoread()
    assert "📝 Todo list is empty" in result


def test_todowrite_add():
    """Test adding todos."""
    # Add a todo
    result = _todowrite("add", "Test", "todo")
    assert "Added todo 1: Test todo" in result
    assert "1" in _current_todos
    assert _current_todos["1"]["text"] == "Test todo"
    assert _current_todos["1"]["state"] == "pending"

    # Add another todo
    result = _todowrite("add", "Second", "task")
    assert "Added todo 2: Second task" in result
    assert "2" in _current_todos


def test_todowrite_add_with_quotes():
    """Test adding todos with quoted text."""
    result = _todowrite("add", '"Complete the project"')
    assert "Added todo 1: Complete the project" in result
    assert _current_todos["1"]["text"] == "Complete the project"


def test_todowrite_update_state():
    """Test updating todo state."""
    # Add a todo first
    _todowrite("add", "Test", "todo")

    # Update state to in_progress
    result = _todowrite("update", "1", "in_progress")
    assert "Updated todo 1 state to: in_progress" in result
    assert _current_todos["1"]["state"] == "in_progress"

    # Update state to completed
    result = _todowrite("update", "1", "completed")
    assert "Updated todo 1 state to: completed" in result
    assert _current_todos["1"]["state"] == "completed"


def test_todowrite_update_paused():
    """Test updating todo state to paused."""
    _todowrite("add", "Test", "todo")

    # Update state to paused
    result = _todowrite("update", "1", "paused")
    assert "Updated todo 1 state to: paused" in result
    assert _current_todos["1"]["state"] == "paused"

    # Verify paused shows correct emoji in list
    result = _todoread()
    assert "⏸️" in result

    # Verify paused counts as incomplete
    from gptme.tools.todo import get_incomplete_todos_summary, has_incomplete_todos

    assert has_incomplete_todos()
    summary = get_incomplete_todos_summary()
    assert "Test todo" in summary


def test_todowrite_update_text():
    """Test updating todo text."""
    # Add a todo first
    _todowrite("add", "Original", "text")

    # Update text
    result = _todowrite("update", "1", "New", "text")
    assert "Updated todo 1 text to: New text" in result
    assert _current_todos["1"]["text"] == "New text"


def test_todowrite_remove():
    """Test removing todos."""
    # Add todos
    _todowrite("add", "First", "todo")
    _todowrite("add", "Second", "todo")

    # Remove first todo
    result = _todowrite("remove", "1")
    assert "Removed todo 1: First todo" in result
    assert "1" not in _current_todos
    assert "2" in _current_todos


def test_todowrite_clear_all():
    """Test clearing all todos."""
    # Add todos
    _todowrite("add", "First", "todo")
    _todowrite("add", "Second", "todo")

    # Clear all
    result = _todowrite("clear")
    assert "Cleared 2 todos" in result
    assert len(_current_todos) == 0


def test_todowrite_clear_completed():
    """Test clearing only completed todos."""
    # Add todos
    _todowrite("add", "First", "todo")
    _todowrite("add", "Second", "todo")
    _todowrite("add", "Third", "todo")

    # Mark some as completed
    _todowrite("update", "1", "completed")
    _todowrite("update", "2", "in_progress")

    # Clear completed
    result = _todowrite("clear", "completed")
    assert "Cleared 1 completed todos" in result
    assert "1" not in _current_todos
    assert "2" in _current_todos
    assert "3" in _current_todos


def test_todoread_with_todos():
    """Test reading todo list with content."""
    # Add todos in different states
    _todowrite("add", "Pending", "task")
    _todowrite("add", "Active", "task")
    _todowrite("add", "Done", "task")

    _todowrite("update", "2", "in_progress")
    _todowrite("update", "3", "completed")

    # Read the list
    result = _todoread()

    assert "Todo List:" in result
    assert "1. 🔲 Pending task" in result
    assert "2. 🔄 Active task" in result
    assert "3. ✅ Done task" in result
    # Check summary exists (order may vary due to Counter)
    assert "Summary: 3 total" in result
    assert "pending: 1" in result
    assert "in_progress: 1" in result
    assert "completed: 1" in result


def test_todowrite_errors():
    """Test error handling."""
    # No operation args
    result = _todowrite("add")
    assert "Error: add requires todo text" in result

    # Update non-existent todo
    result = _todowrite("update", "999", "completed")
    assert "Error: Todo 999 not found" in result

    # Update without parameters
    result = _todowrite("update", "1")
    assert "Error: update requires ID and state/text" in result

    # Remove non-existent todo
    result = _todowrite("remove", "999")
    assert "Error: Todo 999 not found" in result

    # Remove without ID
    result = _todowrite("remove")
    assert "Error: remove requires ID" in result

    # Unknown operation
    result = _todowrite("unknown")
    assert "Error: Unknown operation 'unknown'" in result


def test_todo_id_generation():
    """Test that todo IDs are generated correctly."""
    # Add todos and check IDs
    _todowrite("add", "First")
    assert "1" in _current_todos

    _todowrite("add", "Second")
    assert "2" in _current_todos

    # Remove middle todo
    _todowrite("remove", "1")

    # Next todo should get next available ID
    _todowrite("add", "Third")
    assert "3" in _current_todos


def test_conversation_scoped_storage():
    """Test that todos are stored in conversation scope."""
    # Add a todo
    _todowrite("add", "Test", "todo")
    assert len(_current_todos) == 1

    # Clear the global storage (simulating new conversation)
    _current_todos.clear()

    # Should be empty
    result = _todoread()
    assert "📝 Todo list is empty" in result


def test_execute_todo_read():
    """Test execute_todo with read subcommand."""
    from gptme.tools.todo import execute_todo

    # Test read on empty list
    msgs = list(execute_todo(None, ["read"], None))
    assert len(msgs) == 1
    assert "📝 Todo list is empty" in msgs[0].content

    # Add some todos
    _todowrite("add", "Task one")
    _todowrite("add", "Task two")

    # Test read with content
    msgs = list(execute_todo(None, ["read"], None))
    assert len(msgs) == 1
    assert "Todo List:" in msgs[0].content
    assert "Task one" in msgs[0].content
    assert "Task two" in msgs[0].content


def test_execute_todo_write():
    """Test execute_todo with write subcommand."""
    from gptme.tools.todo import execute_todo

    # Test write with add operations
    content = 'add "First task"\nadd "Second task"'
    msgs = list(execute_todo(content, ["write"], None))
    assert len(msgs) == 1
    assert "Added todo 1" in msgs[0].content
    assert "Added todo 2" in msgs[0].content

    # Verify todos were added
    assert "1" in _current_todos
    assert "2" in _current_todos

    # Test write with update
    msgs = list(execute_todo("update 1 in_progress", ["write"], None))
    assert "Updated todo 1 state to: in_progress" in msgs[0].content
    assert _current_todos["1"]["state"] == "in_progress"


def test_execute_todo_default_read():
    """Test execute_todo defaults to read when no subcommand given."""
    from gptme.tools.todo import execute_todo

    # No args should default to read
    msgs = list(execute_todo(None, None, None))
    assert len(msgs) == 1
    assert "📝 Todo list is empty" in msgs[0].content


def test_execute_todo_invalid_subcommand():
    """Test execute_todo with invalid subcommand."""
    from gptme.tools.todo import execute_todo

    msgs = list(execute_todo(None, ["invalid"], None))
    assert len(msgs) == 1
    assert "Error: Unknown subcommand" in msgs[0].content


def test_todo_helper_for_replay():
    """Test _todo helper function used by replay mechanism."""
    from gptme.tools.todo import _todo

    # Test routing to write operations
    result = _todo("add", "Replay task")
    assert "Added todo 1" in result

    # Test routing to read
    result = _todo("read")
    assert "Todo List:" in result
    assert "Replay task" in result

    # Test other write operations
    result = _todo("update", "1", "completed")
    assert "Updated todo 1 state to: completed" in result

    result = _todo("clear")
    assert "Cleared" in result


@pytest.fixture
def xml_format():
    from gptme.tools.base import get_tool_format, set_tool_format

    prev = get_tool_format()
    set_tool_format("xml")
    yield
    set_tool_format(prev)


def test_replay_ignores_system_prompt_examples(xml_format):
    """Tool examples rendered in the (xml) system prompt must not restore phantom todos.

    Build the example with the real ToolUse XML serializer so the test pins the
    exact format the code parses: if system-message filtering were removed, the
    example would parse as a genuine todo write and this assertion would fail.
    """
    from gptme.message import Message
    from gptme.tools.base import ToolUse
    from gptme.tools.todo import replay_todo_on_session_start

    example = ToolUse(
        "todo", ["write"], 'add "Implement core functionality"'
    ).to_output("xml")
    msgs = [Message("system", example), Message("user", "hi")]
    assert list(replay_todo_on_session_start(None, None, msgs)) == []
    assert not _current_todos


def test_replay_restores_assistant_todos(xml_format):
    from gptme.message import Message
    from gptme.tools.todo import replay_todo_on_session_start

    call = '<tool-use>\n<todo args="write">\nadd "Real task"\n</todo>\n</tool-use>'
    out = list(
        replay_todo_on_session_start(
            None, None, [Message("user", "hi"), Message("assistant", call)]
        )
    )
    assert out and "Restored todo state" in out[0].content
    assert _current_todos["1"]["text"] == "Real task"


def _execute(code=None, args=None, kwargs=None):
    """Run execute_todo and return the message contents."""
    from gptme.tools.todo import execute_todo

    return [msg.content for msg in execute_todo(code, args, kwargs)]


def test_execute_todo_command_kwarg_add():
    """Native tool calls pass the whole command as a single 'command' kwarg."""
    result = _execute(kwargs={"command": 'write add "Implement core functionality"'})
    assert result == ["Added todo 1: Implement core functionality"]
    assert _current_todos["1"]["text"] == "Implement core functionality"


def test_execute_todo_command_kwarg_read():
    """A 'read' command lists the todos instead of silently doing nothing."""
    _todowrite("add", "Existing", "task")
    result = _execute(kwargs={"command": "read"})
    assert "Existing" in result[0]


def test_execute_todo_command_kwarg_multiple_operations():
    """Multiple write operations can be separated by newlines."""
    result = _execute(kwargs={"command": 'write\nadd "First"\nadd "Second"'})
    assert result == ["Added todo 1: First\nAdded todo 2: Second"]
    assert _current_todos["1"]["text"] == "First"
    assert _current_todos["2"]["text"] == "Second"


def test_execute_todo_command_kwarg_write_operation_without_subcommand():
    """A write operation sent without the 'write' subcommand still works."""
    result = _execute(kwargs={"command": 'add "Bare operation"'})
    assert result == ["Added todo 1: Bare operation"]


def test_execute_todo_positional_args_still_work():
    """Markdown-style calls set args positionally and keep working."""
    result = _execute(code='add "Legacy call"', args=["write"])
    assert result == ["Added todo 1: Legacy call"]


def test_execute_todo_no_args_reads():
    """Without a subcommand the list is read (not silently written)."""
    result = _execute()
    assert "📝 Todo list is empty" in result[0]
    assert not _current_todos


@pytest.fixture
def native_tool_format():
    """Use the native ("tool") format, where calls carry a JSON argument object."""
    from gptme.tools.base import get_tool_format, set_tool_format

    prev = get_tool_format()
    set_tool_format("tool")
    yield
    set_tool_format(prev)


def test_replay_restores_native_todo_writes(native_tool_format):
    """Native tool calls carry the command in a kwarg; they must replay too."""
    from gptme.message import Message
    from gptme.tools.todo import replay_todo_on_session_start

    call = '@todo(call_1): {"command": "write add \\"Native task\\""}'
    out = list(
        replay_todo_on_session_start(
            None, None, [Message("user", "hi"), Message("assistant", call)]
        )
    )
    assert out and "Restored todo state" in out[0].content
    assert _current_todos["1"]["text"] == "Native task"


def test_replay_restores_native_bare_write_operation(native_tool_format):
    """A native write operation without the 'write' subcommand replays too."""
    from gptme.message import Message
    from gptme.tools.todo import replay_todo_on_session_start

    call = '@todo(call_1): {"command": "add \\"Bare task\\""}'
    out = list(
        replay_todo_on_session_start(
            None, None, [Message("user", "hi"), Message("assistant", call)]
        )
    )
    assert out and "Restored todo state" in out[0].content
    assert _current_todos["1"]["text"] == "Bare task"


def test_replay_skips_malformed_native_call(native_tool_format):
    """A malformed native call is skipped, not fatal to the replay."""
    from gptme.message import Message
    from gptme.tools.todo import replay_todo_on_session_start

    bad = '@todo(call_1): {"command": null}'
    good = '@todo(call_2): {"command": "write add \\"Survived\\""}'
    out = list(
        replay_todo_on_session_start(
            None,
            None,
            [
                Message("user", "hi"),
                Message("assistant", bad),
                Message("assistant", good),
            ],
        )
    )
    assert out and "Restored todo state" in out[0].content
    assert _current_todos["1"]["text"] == "Survived"


def test_replay_tool_does_not_raise_for_other_tools(tmp_path):
    """Replaying a tool whose module differs from its name must not raise."""
    from gptme.commands.export import _replay_tool
    from gptme.logmanager import Log
    from gptme.message import Message

    log = Log(
        messages=[
            Message("user", "hi"),
            Message("assistant", '@append(call_1): {"path": "x.txt"}'),
        ]
    )
    # append lives in save.py; the replay must not blow up on the import.
    _replay_tool(log, "append")
