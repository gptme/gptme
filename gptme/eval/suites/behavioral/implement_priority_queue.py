"""Behavioral scenario: implement-priority-queue."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "priority_queue.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing PriorityQueue."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_uses_heap(ctx):
    """Should use heapq or a heap-based approach, not naive sorting."""
    content = _get_source(ctx)
    return "heapq" in content


def check_has_push_method(ctx):
    """Should implement push/enqueue method."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in ("push", "enqueue", "insert"):
                return True
    return False


def check_has_pop_method(ctx):
    """Should implement pop/dequeue method."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in ("pop", "dequeue", "extract_min", "extract_max"):
                return True
    return False


def check_has_size_or_len(ctx):
    """Should support checking queue size (method or __len__)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == "__len__":
            return True
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name in ("size", "__len__"):
                return True
    return False


def check_raises_on_empty_pop(ctx):
    """Should raise an exception when popping from an empty queue."""
    content = _get_source(ctx)
    return "IndexError" in content or "raise" in content


PRIORITY_QUEUE_SRC = '''\
"""Task scheduler using a priority queue."""

from typing import Any


class PriorityQueue:
    """Min-heap priority queue for task scheduling.

    Lower numeric priority values are dequeued first (priority 1 before 10).
    When two items share the same priority, a FIFO tie-breaking order is used.

    Args:
        initial: Optional list of (priority, item) pairs to pre-load.
    """

    def __init__(self, initial: list[tuple[int, Any]] | None = None):
        self._data: list[tuple[int, int, Any]] = []
        self._counter = 0
        # TODO: implement priority queue

    def push(self, priority: int, item: Any) -> None:
        """Insert an item with the given priority."""
        # TODO: implement
        self._data.append((priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Any:
        """Remove and return the highest-priority (lowest number) item.

        Raises:
            IndexError: If the queue is empty.
        """
        # TODO: implement
        raise IndexError("pop from empty queue")

    def peek(self) -> Any:
        """Return the highest-priority item without removing it.

        Raises:
            IndexError: If the queue is empty.
        """
        # TODO: implement
        raise IndexError("peek from empty queue")

    def __len__(self) -> int:
        """Return the number of items in the queue."""
        return len(self._data)

    def is_empty(self) -> bool:
        """Return True if the queue contains no items."""
        return len(self._data) == 0
'''

TEST_PRIORITY_QUEUE_SRC = '''\
import pytest
from priority_queue import PriorityQueue


def test_push_and_pop_single():
    """Should push an item and pop it back."""
    pq = PriorityQueue()
    pq.push(1, "task-a")
    assert pq.pop() == "task-a"


def test_priority_ordering():
    """Items with lower priority values should be dequeued first."""
    pq = PriorityQueue()
    pq.push(10, "low-priority")
    pq.push(1, "high-priority")
    pq.push(5, "mid-priority")

    assert pq.pop() == "high-priority"
    assert pq.pop() == "mid-priority"
    assert pq.pop() == "low-priority"


def test_fifo_for_same_priority():
    """Items with equal priority should be dequeued in FIFO order."""
    pq = PriorityQueue()
    pq.push(3, "first-in")
    pq.push(3, "second-in")
    pq.push(3, "third-in")

    assert pq.pop() == "first-in"
    assert pq.pop() == "second-in"
    assert pq.pop() == "third-in"


def test_pop_from_empty_raises():
    """Popping from an empty queue should raise IndexError."""
    pq = PriorityQueue()
    with pytest.raises(IndexError):
        pq.pop()


def test_peek_returns_highest_priority():
    """Peek should return the highest-priority item without removing it."""
    pq = PriorityQueue()
    pq.push(10, "low")
    pq.push(1, "high")
    pq.push(5, "mid")

    assert pq.peek() == "high"
    assert len(pq) == 3  # peek should not remove


def test_peek_from_empty_raises():
    """Peeking at an empty queue should raise IndexError."""
    pq = PriorityQueue()
    with pytest.raises(IndexError):
        pq.peek()


def test_size_tracking():
    """len() should reflect the number of items."""
    pq = PriorityQueue()
    assert len(pq) == 0

    pq.push(1, "a")
    assert len(pq) == 1

    pq.push(2, "b")
    assert len(pq) == 2

    pq.pop()
    assert len(pq) == 1


def test_is_empty():
    """is_empty should correctly report empty state."""
    pq = PriorityQueue()
    assert pq.is_empty() is True

    pq.push(1, "item")
    assert pq.is_empty() is False

    pq.pop()
    assert pq.is_empty() is True


def test_mixed_priorities_interleaved():
    """Queue should correctly order after interleaved push/pop operations."""
    pq = PriorityQueue()
    pq.push(5, "a")
    pq.push(1, "b")
    pq.push(10, "c")

    assert pq.pop() == "b"  # priority 1
    pq.push(3, "d")
    assert pq.pop() == "d"  # priority 3 < 5
    assert pq.pop() == "a"  # priority 5
    assert pq.pop() == "c"  # priority 10
    assert pq.is_empty()
'''

test: "EvalSpec" = {
    "name": "implement-priority-queue",
    "task_type": "structured_process",
    "files": {
        "priority_queue.py": PRIORITY_QUEUE_SRC,
        "test_priority_queue.py": TEST_PRIORITY_QUEUE_SRC,
    },
    "run": "python3 -m pytest test_priority_queue.py -v --tb=short 2>&1",
    "prompt": (
        "The `PriorityQueue` class in `priority_queue.py` has stub methods "
        "that don't use a proper heap — items are stored in a plain list.\n\n"
        "The test suite in `test_priority_queue.py` is failing. Implement a "
        "min-heap-based priority queue using Python's `heapq` module:\n\n"
        "- `push(priority, item)`: Insert with given priority (lower = higher priority)\n"
        "- `pop()`: Remove and return the highest-priority item (lowest number)\n"
        "- `peek()`: Return highest-priority item without removing it\n"
        "- `__len__()`: Return number of items\n"
        "- `is_empty()`: Return True when no items remain\n"
        "- Same-priority items must follow FIFO order (use a counter as tiebreaker)\n"
        "- Raise IndexError on pop/peek from empty queue\n"
        "- Use only the Python standard library (heapq)\n\n"
        "After implementing, run the tests to verify they all pass:\n"
        "  python3 -m pytest test_priority_queue.py -v --tb=short\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "uses heapq": check_uses_heap,
        "has push method": check_has_push_method,
        "has pop method": check_has_pop_method,
        "has size or __len__": check_has_size_or_len,
        "raises on empty pop": check_raises_on_empty_pop,
    },
}
