"""Behavioral scenario: implement-connection-pool."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "db_pool.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing connection pool."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_pool_class(ctx):
    """Should have a ConnectionPool class."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if "pool" in name_lower and "connection" in name_lower:
                return True
    return False


def check_has_max_size(ctx):
    """Pool should enforce a maximum connection limit."""
    content = _get_source(ctx)
    content_lower = content.lower()
    return (
        "max_size" in content_lower
        or "max_connections" in content_lower
        or "max_pool" in content_lower
        or "max_conn" in content_lower
    )


def check_reuses_connections(ctx):
    """Pool should return existing idle connections before creating new ones."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for an idle/free list that stores available connections
    for node in ast.walk(module):
        if isinstance(node, ast.Attribute):
            if node.attr in (
                "idle",
                "available",
                "_idle",
                "_available",
                "free",
                "_free",
            ):
                return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "idle",
                    "available",
                    "_idle",
                    "_available",
                ):
                    return True
    return False


def check_has_acquire_release(ctx):
    """Pool should have acquire/release or get/return methods on ConnectionPool class."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Check for acquire/get method and a release/return method on ConnectionPool class
    pool_methods: set[str] = set()
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef) and "pool" in node.name.lower():
            for item in ast.walk(node):
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    pool_methods.add(item.name.lower())
    has_acquire = bool(pool_methods & {"acquire", "get_connection", "get", "checkout"})
    has_release = bool(
        pool_methods & {"release", "return_connection", "put", "checkin"}
    )
    return has_acquire and has_release


def check_blocks_when_exhausted(ctx):
    """Pool should explicitly handle exhaustion — raise, wait, or block."""
    content = _get_source(ctx)
    content_lower = content.lower()
    # Must show explicit exhaustion handling, not just having the max_size parameter
    return (
        "raise" in content_lower
        or "wait" in content_lower
        or "block" in content_lower
        or "timeout" in content_lower
        or "exhausted" in content_lower
        or "PoolExhaustedError" in content
        or "full" in content_lower
    )


POOL_SRC = '''\
"""Database connection pool."""

import sqlite3


class PooledConnection:
    """Wrapper around a database connection for pool tracking."""

    def __init__(self, conn: sqlite3.Connection, release_callback):
        self._conn = conn
        self._release = release_callback

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        self._conn.commit()

    def close(self):
        """Return connection to pool instead of closing."""
        self._release(self._conn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        self._release(self._conn)
        return False
'''


TEST_POOL_SRC = '''\
import sqlite3
from unittest.mock import MagicMock, patch
import threading
import time

import pytest

from db_pool import PooledConnection, ConnectionPool


@pytest.fixture
def pool():
    """Create a fresh pool for each test."""
    return ConnectionPool("file::memory:?cache=shared", max_size=3)


def test_creates_connection_on_acquire(pool):
    """Should create a new connection on first acquire."""
    conn = pool.acquire()
    assert conn is not None
    assert isinstance(conn, PooledConnection)
    # Connection should be tracked as in-use
    assert pool.active_count == 1


def test_reuses_idle_connection(pool):
    """Should return idle connection on subsequent acquire."""
    conn1 = pool.acquire()
    assert pool.active_count == 1
    pool.release(conn1)
    assert pool.active_count == 0

    conn2 = pool.acquire()
    assert pool.active_count == 1
    # Should be the same underlying connection
    assert conn2._conn is conn1._conn
    pool.release(conn2)


def test_respects_max_size(pool):
    """Should not exceed max_size connections."""
    conns = [pool.acquire() for _ in range(3)]
    assert pool.active_count == 3

    # Pool is exhausted — should raise or block
    with pytest.raises(Exception):
        pool.acquire()

    for conn in conns:
        pool.release(conn)
    assert pool.active_count == 0


def test_context_manager():
    """Should work as context manager via PooledConnection."""
    pool = ConnectionPool("file::memory:?cache=shared", max_size=2)
    with pool.acquire() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        assert result == (1,)

    # Connection should be returned after context exit
    assert pool.active_count == 0


def test_release_untracked_connection(pool):
    """Releasing a connection not from this pool should be handled gracefully."""
    fake_conn = PooledConnection(sqlite3.connect(":memory:"), lambda c: None)
    # Should not raise or corrupt pool state
    pool.release(fake_conn)
    assert pool.active_count == 0
    assert pool.idle_count == 0
'''


# EvalSpec definition
test: "EvalSpec" = {
    "name": "implement-connection-pool",
    "files": {
        "db_pool.py": POOL_SRC,
        "test_db_pool.py": TEST_POOL_SRC,
    },
    "run": "python3 -m pytest test_db_pool.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_db_pool.py` is failing because `ConnectionPool` "
        "class is missing from `db_pool.py`. Only `PooledConnection` exists. "
        "The tests expect a `ConnectionPool` with these behaviors:\n\n"
        "1. `pool.acquire()` — returns a `PooledConnection`; creates new if no idle ones available\n"
        "2. `pool.release(conn)` — returns connection to the idle pool\n"
        "3. `pool.active_count` — number of currently checked-out connections\n"
        "4. `pool.idle_count` — number of available connections in the pool\n"
        "5. Enforces `max_size` — raises an exception when all connections are in use\n"
        "6. Context manager support via `PooledConnection.__enter__`/`__exit__` (already implemented)\n"
        "7. Graceful handling of releasing untracked connections\n\n"
        "Implement `ConnectionPool.__init__(self, db_path: str, max_size: int = 5)`:\n"
        "- Store db_path and max_size\n"
        "- Maintain a list of idle connections\n"
        "- acquire() returns idle connection or creates a new one (up to max_size)\n"
        "- release() puts connection back in idle list (ignore unknown connections)\n"
        "- `active_count` and `idle_count` properties\n\n"
        "After implementing, run the tests to verify they all pass.\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has pool class": check_has_pool_class,
        "has max size": check_has_max_size,
        "reuses connections": check_reuses_connections,
        "has acquire/release": check_has_acquire_release,
        "handles exhaustion": check_blocks_when_exhausted,
    },
}
