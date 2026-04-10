"""Behavioral scenario: implement-lru-cache."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "cache.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing LRU cache."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_cache_structure(ctx):
    """Should have a cache data structure (dict, OrderedDict, or custom)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Accept any cache structure: dict, OrderedDict, functools.lru_cache, etc.
    if "OrderedDict" in content or "lru_cache" in content:
        return True
    # Look for dict assignment as a cache store (e.g. self._cache = {})
    for node in ast.walk(module):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(
                    node.value, (ast.Dict, ast.Call)
                ):
                    if "cache" in target.attr.lower():
                        return True
    return "_cache" in content or "self.cache" in content


def check_has_capacity_limit(ctx):
    """Should respect the max_size capacity limit."""
    content = _get_source(ctx)
    return (
        "max_size" in content
        or "maxsize" in content
        or "_max_size" in content
        or "capacity" in content.lower()
    )


def check_has_eviction(ctx):
    """Should evict entries when cache is full."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for pop/del/popitem to evict entries
    for node in ast.walk(module):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr in (
                "pop",
                "popitem",
                "remove",
            ):
                return True
        if isinstance(node, ast.Delete):
            return True
    return "popitem" in content or ".pop(" in content or "del " in content


def check_has_recency_tracking(ctx):
    """Should track access order to implement LRU eviction."""
    content = _get_source(ctx)
    # OrderedDict.move_to_end is the canonical stdlib approach
    # Also accept manual order tracking or deque
    return (
        "move_to_end" in content
        or "OrderedDict" in content
        or "deque" in content
        or "_order" in content
        or "lru_cache" in content
    )


CACHE_SRC = '''\
"""Data access layer with LRU caching."""

from typing import Any


class DataStore:
    """Simulates a database or slow data source."""

    def __init__(self):
        self._call_count = 0
        self._data = {
            "user:1": {"id": 1, "name": "Alice"},
            "user:2": {"id": 2, "name": "Bob"},
            "user:3": {"id": 3, "name": "Carol"},
            "user:4": {"id": 4, "name": "Dave"},
            "user:5": {"id": 5, "name": "Eve"},
        }

    @property
    def call_count(self) -> int:
        return self._call_count

    def get(self, key: str) -> Any:
        """Fetch a record (simulates an expensive operation)."""
        self._call_count += 1
        return self._data.get(key)


class CachedDataStore:
    """DataStore wrapper with LRU (Least Recently Used) caching.

    Args:
        store: The underlying data store.
        max_size: Maximum number of entries to keep in cache.
    """

    def __init__(self, store: DataStore, max_size: int = 3):
        self._store = store
        self._max_size = max_size

    def get(self, key: str) -> Any:
        """Return the record for *key*, fetching from store on cache miss.

        On a cache hit the entry's recency is updated so it is not the
        next candidate for eviction.  When the cache is full a new entry
        replaces the least-recently-used one.
        """
        # TODO: implement LRU caching
        return self._store.get(key)
'''

TEST_CACHE_SRC = '''\
import pytest
from cache import CachedDataStore, DataStore


def test_returns_correct_value():
    """Should return correct values from the underlying store."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=3)

    assert cache.get("user:1") == {"id": 1, "name": "Alice"}
    assert cache.get("user:2") == {"id": 2, "name": "Bob"}


def test_cache_hit_avoids_store_call():
    """Repeated calls with the same key must not hit the store again."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=3)

    cache.get("user:1")
    assert store.call_count == 1

    cache.get("user:1")
    assert store.call_count == 1  # no additional store call


def test_different_keys_each_hit_store_once():
    """Each distinct key should hit the store exactly once."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=5)

    for _ in range(3):
        cache.get("user:1")
        cache.get("user:2")
        cache.get("user:3")

    assert store.call_count == 3


def test_lru_eviction_policy():
    """Least-recently-used entry is evicted when cache is at capacity."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=2)

    # Fill cache: user:1 then user:2
    cache.get("user:1")  # store call_count = 1
    cache.get("user:2")  # store call_count = 2

    # Re-access user:1 → it becomes the most-recently-used
    cache.get("user:1")  # cache hit, call_count stays 2

    # Add user:3 — cache is full; user:2 is LRU and should be evicted
    cache.get("user:3")  # store call_count = 3

    # user:1 must still be cached (recently used)
    cache.get("user:1")
    assert store.call_count == 3  # no new store call

    # user:2 must have been evicted
    cache.get("user:2")
    assert store.call_count == 4  # store called again


def test_cache_capacity_not_exceeded():
    """Store is called for each unique key even when cache is full."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=2)

    cache.get("user:1")
    cache.get("user:2")
    cache.get("user:3")  # evicts one entry

    # Three distinct keys → three store calls
    assert store.call_count == 3


def test_returns_none_for_missing_key():
    """Should return None for keys absent from the store."""
    store = DataStore()
    cache = CachedDataStore(store, max_size=3)

    assert cache.get("user:99") is None
'''

test: "EvalSpec" = {
    "name": "implement-lru-cache",
    "files": {
        "cache.py": CACHE_SRC,
        "test_cache.py": TEST_CACHE_SRC,
    },
    "run": "python3 -m pytest test_cache.py -v --tb=short 2>&1",
    "prompt": (
        "The `CachedDataStore` class in `cache.py` has a stub `get()` method "
        "that always calls the underlying store — caching is not implemented yet.\n\n"
        "The test suite in `test_cache.py` is failing. Implement LRU "
        "(Least Recently Used) caching inside `CachedDataStore.get()`:\n\n"
        "- Return cached results for keys already seen (no store call on hit)\n"
        "- When the cache is full (reached `max_size`), evict the least-recently-used "
        "entry before inserting a new one\n"
        "- Re-accessing a cached key makes it the most-recently-used "
        "(it won't be the next eviction candidate)\n"
        "- Use only the Python standard library (e.g. `collections.OrderedDict`)\n\n"
        "After implementing, run the tests to verify they all pass:\n"
        "  python3 -m pytest test_cache.py -v --tb=short\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has cache structure": check_has_cache_structure,
        "has capacity limit": check_has_capacity_limit,
        "has eviction logic": check_has_eviction,
        "tracks access recency": check_has_recency_tracking,
    },
}
