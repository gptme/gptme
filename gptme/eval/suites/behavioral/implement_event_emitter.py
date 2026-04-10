"""Behavioral scenario: implement-event-emitter (Observer pattern)."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "emitter.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing EventEmitter."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_handler_storage(ctx):
    """EventEmitter should store handlers in a dict or defaultdict."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for dict or defaultdict assignment in __init__
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == "EventEmitter":
            for child in ast.walk(node):
                if isinstance(child, ast.FunctionDef) and child.name == "__init__":
                    for stmt in ast.walk(child):
                        if isinstance(stmt, ast.Assign | ast.AnnAssign):
                            # Check that the value is a dict/defaultdict/similar
                            val = getattr(stmt, "value", None)
                            if val and isinstance(val, ast.Call):
                                func = getattr(val, "func", None)
                                if func:
                                    name = getattr(func, "id", "") or getattr(
                                        func, "attr", ""
                                    )
                                    if name in ("dict", "defaultdict", "Dict"):
                                        return True
                            elif val and isinstance(val, ast.Dict):
                                return True
    return False


def check_has_on_method(ctx):
    """EventEmitter should have an 'on' method for subscribing handlers."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == "EventEmitter":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "on":
                    return True
    return False


def check_has_emit_method(ctx):
    """EventEmitter should have an 'emit' method that calls registered handlers."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == "EventEmitter":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "emit":
                    # Verify the body calls something (handlers)
                    for stmt in ast.walk(child):
                        if isinstance(stmt, ast.Call):
                            return True
    return False


def check_has_off_method(ctx):
    """EventEmitter should have an 'off' method for unsubscribing handlers."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and node.name == "EventEmitter":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "off":
                    return True
    return False


EMITTER_SRC = """\
\"\"\"Simple event emitter (Observer pattern) implementation.\"\"\"

from typing import Callable


class EventEmitter:
    \"\"\"Emit named events and notify registered listeners.\"\"\"

    def on(self, event: str, handler: Callable) -> None:
        \"\"\"Subscribe handler to event.

        Args:
            event: Event name to listen for.
            handler: Callable invoked with emit(*args, **kwargs).
        \"\"\"
        # TODO: store handler for event

    def off(self, event: str, handler: Callable) -> None:
        \"\"\"Unsubscribe handler from event.

        Args:
            event: Event name.
            handler: Previously registered callable to remove.
        \"\"\"
        # TODO: remove handler from event (no-op if not registered)

    def emit(self, event: str, *args, **kwargs) -> None:
        \"\"\"Fire all handlers registered for event.

        Args:
            event: Event name to emit.
            *args: Positional arguments forwarded to each handler.
            **kwargs: Keyword arguments forwarded to each handler.
        \"\"\"
        # TODO: call each registered handler with args and kwargs
"""

TEST_EMITTER_SRC = """\
import pytest

from emitter import EventEmitter


def test_on_and_emit_basic():
    \"\"\"Subscribing a handler and emitting should call it.\"\"\"
    emitter = EventEmitter()
    received = []
    emitter.on("data", lambda x: received.append(x))
    emitter.emit("data", 42)
    assert received == [42]


def test_emit_with_multiple_args():
    \"\"\"Emit should forward all positional and keyword arguments.\"\"\"
    emitter = EventEmitter()
    calls = []
    emitter.on("msg", lambda a, b, tag="": calls.append((a, b, tag)))
    emitter.emit("msg", "hello", "world", tag="greeting")
    assert calls == [("hello", "world", "greeting")]


def test_multiple_handlers_for_same_event():
    \"\"\"All handlers subscribed to an event should be called.\"\"\"
    emitter = EventEmitter()
    log = []
    emitter.on("tick", lambda: log.append("A"))
    emitter.on("tick", lambda: log.append("B"))
    emitter.emit("tick")
    assert sorted(log) == ["A", "B"]


def test_emit_unknown_event_is_noop():
    \"\"\"Emitting an event with no listeners should not raise.\"\"\"
    emitter = EventEmitter()
    emitter.emit("no-listeners")  # should not raise


def test_off_removes_handler():
    \"\"\"off() should stop handler from receiving future events.\"\"\"
    emitter = EventEmitter()
    calls = []

    def handler(x):
        calls.append(x)

    emitter.on("update", handler)
    emitter.emit("update", 1)
    emitter.off("update", handler)
    emitter.emit("update", 2)
    assert calls == [1]  # second emit should NOT call handler


def test_off_unregistered_handler_is_noop():
    \"\"\"Calling off() for a handler not registered should not raise.\"\"\"
    emitter = EventEmitter()

    def handler():
        pass

    emitter.off("missing-event", handler)  # should not raise
    emitter.on("evt", handler)
    emitter.off("evt", handler)
    emitter.off("evt", handler)  # second removal should not raise


def test_independent_event_namespaces():
    \"\"\"Handlers for different events must not interfere with each other.\"\"\"
    emitter = EventEmitter()
    a_calls, b_calls = [], []
    emitter.on("a", lambda: a_calls.append(1))
    emitter.on("b", lambda: b_calls.append(1))
    emitter.emit("a")
    assert a_calls == [1]
    assert b_calls == []  # 'b' handler should NOT have been called
"""


test: "EvalSpec" = {
    "name": "implement-event-emitter",
    "files": {
        "emitter.py": EMITTER_SRC,
        "test_emitter.py": TEST_EMITTER_SRC,
    },
    "run": "python3 -m pytest test_emitter.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_emitter.py` is failing because `EventEmitter` in "
        "`emitter.py` has stub methods that do nothing. Implement the Observer "
        "pattern so all tests pass:\n\n"
        "1. `on(event, handler)` — register a callable to be called when `event` is emitted\n"
        "2. `emit(event, *args, **kwargs)` — call all handlers registered for `event`, "
        "forwarding args and kwargs\n"
        "3. `off(event, handler)` — unregister a handler; no-op if not registered\n\n"
        "Requirements:\n"
        "- Multiple handlers per event are supported\n"
        "- Emitting an event with no listeners must not raise\n"
        "- Removing an unregistered handler must not raise\n"
        "- Each event name has its own independent set of handlers\n\n"
        "After implementing, run the tests to verify they all pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "stores handlers in dict/defaultdict": check_has_handler_storage,
        "has on() method": check_has_on_method,
        "has emit() method": check_has_emit_method,
        "has off() method": check_has_off_method,
    },
}
