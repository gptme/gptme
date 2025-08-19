"""Tests for the todo tools."""

import pytest

from gptme.tools.todo import _todoread, _todowrite, _current_todos, TodoItem


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
    assert "✅ Added todo 1: Test todo" in result
    assert "1" in _current_todos
    assert _current_todos["1"]["text"] == "Test todo"
    assert _current_todos["1"]["state"] == "pending"

    # Add another todo
    result = _todowrite("add", "Second", "task")
    assert "✅ Added todo 2: Second task" in result
    assert "2" in _current_todos


def test_todowrite_add_with_quotes():
    """Test adding todos with quoted text."""
    result = _todowrite("add", '"Complete the project"')
    assert "✅ Added todo 1: Complete the project" in result
    assert _current_todos["1"]["text"] == "Complete the project"


def test_todowrite_update_state():
    """Test updating todo state."""
    # Add a todo first
    _todowrite("add", "Test", "todo")

    # Update state to in_progress
    result = _todowrite("update", "1", "in_progress")
    assert "✅ Updated todo 1 state to: in_progress" in result
    assert _current_todos["1"]["state"] == "in_progress"

    # Update state to completed
    result = _todowrite("update", "1", "completed")
    assert "✅ Updated todo 1 state to: completed" in result
    assert _current_todos["1"]["state"] == "completed"


def test_todowrite_update_text():
    """Test updating todo text."""
    # Add a todo first
    _todowrite("add", "Original", "text")

    # Update text
    result = _todowrite("update", "1", "New", "text")
    assert "✅ Updated todo 1 text to: New text" in result
    assert _current_todos["1"]["text"] == "New text"


def test_todowrite_remove():
    """Test removing todos."""
    # Add todos
    _todowrite("add", "First", "todo")
    _todowrite("add", "Second", "todo")

    # Remove first todo
    result = _todowrite("remove", "1")
    assert "✅ Removed todo 1: First todo" in result
    assert "1" not in _current_todos
    assert "2" in _current_todos


def test_todowrite_clear_all():
    """Test clearing all todos."""
    # Add todos
    _todowrite("add", "First", "todo")
    _todowrite("add", "Second", "todo")

    # Clear all
    result = _todowrite("clear")
    assert "✅ Cleared 2 todos" in result
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
    assert "✅ Cleared 1 completed todos" in result
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

    assert "📝 Current Todo List:" in result
    assert "🔲 Pending:" in result
    assert "1. Pending task" in result
    assert "🔄 In Progress:" in result
    assert "2. Active task" in result
    assert "✅ Completed:" in result
    assert "3. Done task" in result
    assert "Summary: 3 total (1 completed, 1 in progress, 1 pending)" in result


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
