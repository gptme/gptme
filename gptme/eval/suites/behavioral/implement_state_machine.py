"""Behavioral scenario: implement-state-machine (Finite State Machine)."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "task_state.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing the state machine."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_state_class(ctx):
    """Should have a TaskStateMachine (or similar) class definition."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if (
            isinstance(node, ast.ClassDef)
            and "State" in node.name
            and "Machine" in node.name
        ):
            return True
    return False


def check_has_transition_method(ctx):
    """State machine should have a transition() or similar method."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and "State" in node.name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    name = child.name
                    if name in ("transition", "advance", "move_to", "go_to"):
                        return True
    return False


def check_has_allowed_transitions(ctx):
    """Should define allowed transitions (dict, set, or method-based)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        # Check for dict literal with state strings as keys
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if key.value in (
                        "backlog",
                        "todo",
                        "active",
                        "waiting",
                        "done",
                        "cancelled",
                    ):
                        return True
        # Check for variable assignments named "allowed*" or "transitions*"
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if hasattr(target, "id") and any(
                    kw in target.id.lower() for kw in ("allowed", "transition")
                ):
                    return True
    return False


def check_rejects_invalid_transition(ctx):
    """Should raise an error or return False for invalid transitions."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for raise statements or boolean returns in transition methods
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and "State" in node.name:
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name in (
                    "transition",
                    "advance",
                    "move_to",
                    "go_to",
                ):
                    for stmt in ast.walk(child):
                        if isinstance(stmt, ast.Raise):
                            return True
                        if isinstance(stmt, ast.Return) and stmt.value is not None:
                            return True
    return False


def check_has_get_state(ctx):
    """Should expose a way to get the current state."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and "State" in node.name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name in (
                    "get_state",
                    "state",
                    "current",
                    "status",
                ):
                    return True
                if isinstance(child, ast.AnnAssign) and hasattr(child.target, "id"):
                    if child.target.id in ("state", "current", "_state"):
                        return True
    return False


STATE_MACHINE_SRC = """\
\"\"\"Task state machine with transition validation.\"\"\"

from enum import Enum


class TaskState(str, Enum):
    BACKLOG = "backlog"
    TODO = "todo"
    ACTIVE = "active"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"


class InvalidTransitionError(Exception):
    pass


class TaskStateMachine:
    \"\"\"Finite state machine for task lifecycle management.

    Supports these transitions:
        backlog  -> todo, cancelled
        todo     -> active, cancelled
        active   -> waiting, done, cancelled
        waiting  -> active, cancelled
        done     -> (terminal)
        cancelled -> (terminal)
    \"\"\"

    def __init__(self, initial_state: TaskState = TaskState.BACKLOG):
        self._state = initial_state

    # TODO: implement transition() method that validates state changes

    # TODO: implement get_state() or a state property

    # TODO: define allowed transitions and raise InvalidTransitionError
    # for disallowed changes
"""


TEST_STATE_MACHINE_SRC = """\
import pytest

from task_state import (
    InvalidTransitionError,
    TaskState,
    TaskStateMachine,
)


def test_initial_state():
    \"\"\"Machine starts in the given initial state.\"\"\"
    sm = TaskStateMachine(TaskState.TODO)
    assert sm.state == TaskState.TODO

    sm2 = TaskStateMachine()
    assert sm2.state == TaskState.BACKLOG


def test_backlog_to_todo():
    \"\"\"backlog -> todo should succeed.\"\"\"
    sm = TaskStateMachine(TaskState.BACKLOG)
    sm.transition(TaskState.TODO)
    assert sm.state == TaskState.TODO


def test_todo_to_active():
    \"\"\"todo -> active should succeed.\"\"\"
    sm = TaskStateMachine(TaskState.TODO)
    sm.transition(TaskState.ACTIVE)
    assert sm.state == TaskState.ACTIVE


def test_active_to_done():
    \"\"\"active -> done should succeed.\"\"\"
    sm = TaskStateMachine(TaskState.ACTIVE)
    sm.transition(TaskState.DONE)
    assert sm.state == TaskState.DONE


def test_active_to_waiting_and_back():
    \"\"\"active -> waiting -> active round-trip should succeed.\"\"\"
    sm = TaskStateMachine(TaskState.ACTIVE)
    sm.transition(TaskState.WAITING)
    assert sm.state == TaskState.WAITING
    sm.transition(TaskState.ACTIVE)
    assert sm.state == TaskState.ACTIVE


def test_backlog_to_active_rejected():
    \"\"\"backlog -> active should raise InvalidTransitionError.\"\"\"
    sm = TaskStateMachine(TaskState.BACKLOG)
    with pytest.raises(InvalidTransitionError):
        sm.transition(TaskState.ACTIVE)


def test_active_to_backlog_rejected():
    \"\"\"active -> backlog should raise InvalidTransitionError.\"\"\"
    sm = TaskStateMachine(TaskState.ACTIVE)
    with pytest.raises(InvalidTransitionError):
        sm.transition(TaskState.BACKLOG)


def test_done_to_active_rejected():
    \"\"\"done is terminal — cannot transition back to active.\"\"\"
    sm = TaskStateMachine(TaskState.ACTIVE)
    sm.transition(TaskState.DONE)
    with pytest.raises(InvalidTransitionError):
        sm.transition(TaskState.ACTIVE)


def test_cancelled_is_terminal():
    \"\"\"cancelled is terminal — cannot transition out.\"\"\"
    sm = TaskStateMachine(TaskState.TODO)
    sm.transition(TaskState.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        sm.transition(TaskState.TODO)


def test_any_state_to_cancelled():
    \"\"\"Every non-terminal state can transition to cancelled.\"\"\"
    for initial in (TaskState.BACKLOG, TaskState.TODO, TaskState.ACTIVE, TaskState.WAITING):
        sm = TaskStateMachine(initial)
        sm.transition(TaskState.CANCELLED)
        assert sm.state == TaskState.CANCELLED


def test_invalid_state_type_raises():
    \"\"\"Passing a non-TaskState value should raise TypeError or ValueError.\"\"\"
    sm = TaskStateMachine()
    with pytest.raises((TypeError, ValueError)):
        sm.transition("not_a_state")


def test_full_lifecycle():
    \"\"\"Complete lifecycle: backlog -> todo -> active -> waiting -> active -> done.\"\"\"
    sm = TaskStateMachine()
    assert sm.state == TaskState.BACKLOG
    sm.transition(TaskState.TODO)
    sm.transition(TaskState.ACTIVE)
    sm.transition(TaskState.WAITING)
    sm.transition(TaskState.ACTIVE)
    sm.transition(TaskState.DONE)
    assert sm.state == TaskState.DONE
"""


test: "EvalSpec" = {
    "name": "implement-state-machine",
    "files": {
        "task_state.py": STATE_MACHINE_SRC,
        "test_task_state.py": TEST_STATE_MACHINE_SRC,
    },
    "run": "python3 -m pytest test_task_state.py -v --tb=short 2>&1",
    "prompt": (
        "The `TaskStateMachine` class in `task_state.py` has stub methods — "
        "the `transition()` method and `state` property are not implemented. "
        "The test suite in `test_task_state.py` is failing.\n\n"
        "Implement a finite state machine for task lifecycle management:\n\n"
        "Supported transitions:\n"
        "- backlog  -> todo, cancelled\n"
        "- todo     -> active, cancelled\n"
        "- active   -> waiting, done, cancelled\n"
        "- waiting  -> active, cancelled\n"
        "- done     -> (terminal — no transitions out)\n"
        "- cancelled -> (terminal — no transitions out)\n\n"
        "Requirements:\n"
        "- `transition(target_state)` validates the transition and updates state\n"
        "- Raise `InvalidTransitionError` for disallowed transitions\n"
        "- Provide a `state` property to read the current state\n"
        "- Validate that `target_state` is a `TaskState` enum member\n\n"
        "After implementing, run the tests:\n"
        "  python3 -m pytest test_task_state.py -v --tb=short\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has state machine class": check_has_state_class,
        "has transition method": check_has_transition_method,
        "defines allowed transitions": check_has_allowed_transitions,
        "rejects invalid transitions": check_rejects_invalid_transition,
        "exposes current state": check_has_get_state,
    },
}
