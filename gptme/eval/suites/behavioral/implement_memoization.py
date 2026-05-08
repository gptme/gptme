"""Behavioral scenario: implement-memoization."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "memo.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing memoization."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_cache(ctx):
    """Should have a cache data structure (dict or similar)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for a dict-based cache store (e.g. self._cache = {}, cache = {})
    for node in ast.walk(module):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name | ast.Attribute):
                    name = (
                        target.attr if isinstance(target, ast.Attribute) else target.id
                    )
                    if "cache" in name.lower():
                        if isinstance(node.value, ast.Dict | ast.Call):
                            return True
    return "_cache" in content or "cache = {}" in content


def check_has_args_caching(ctx):
    """Should cache based on function arguments."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for use of *args or **kwargs or explicit parameters in cache lookup
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get":
                # cache.get(...) — good sign of argument-based caching
                return True
            if isinstance(func, ast.Name) and func.id == "tuple":
                # tuple(args) for hashable cache key
                return True
    # Also accept args/kwargs in source
    return "*args" in content or "**kwargs" in content or "args" in content


def check_is_decorator(ctx):
    """Should be implemented as a decorator (returns a wrapper)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Check for nested function definition (decorator pattern: memoize defines wrapper inside)
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    return True
    return False


MEMO_SRC = '''\
"""Memoization decorator for caching expensive function calls."""

from functools import wraps


def memoize(fn):
    """Cache results of *fn* based on its arguments.

    Subsequent calls with the same arguments return the cached result
    without re-executing *fn*.
    """
    # TODO: implement memoization
    return fn


@memoize
def fibonacci(n):
    """Compute the nth Fibonacci number (slow recursive implementation)."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


@memoize
def factorial(n):
    """Compute n! (slow recursive implementation)."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def computeheavy(x, y):
    """A non-decorated function to verify memoize works on any callable."""
    return x ** y
'''

TEST_MEMO_SRC = '''\
import pytest
from memo import memoize, fibonacci, factorial, computeheavy


def test_memoize_fibonacci():
    """fibonacci should produce correct values with memoization."""
    # Reset by reloading
    from importlib import reload
    import memo
    reload(memo)
    fib = memo.fibonacci

    assert fib(0) == 0
    assert fib(1) == 1
    assert fib(5) == 5
    assert fib(10) == 55
    assert fib(15) == 610


def test_memoize_caches_fibonacci():
    """Calling fibonacci with the same argument should not recompute."""
    from importlib import reload
    import memo
    reload(memo)
    fib = memo.fibonacci

    # Call once
    result1 = fib(20)
    # Call again with same value - should be cached
    result2 = fib(20)
    assert result1 == result2


def test_memoize_factorial():
    """factorial should produce correct values with memoization."""
    from importlib import reload
    import memo
    reload(memo)
    fac = memo.factorial

    assert fac(0) == 1
    assert fac(1) == 1
    assert fac(5) == 120
    assert fac(10) == 3628800


def test_memoize_caches_factorial():
    """Calling factorial with the same argument should not recompute."""
    from importlib import reload
    import memo
    reload(memo)
    fac = memo.factorial

    result1 = fac(12)
    result2 = fac(12)
    assert result1 == result2


def test_memoize_decorator_on_regular_function():
    """memoize should work as a decorator on any function."""
    from importlib import reload
    import memo
    reload(memo)

    # computeheavy is not decorated in the source; test that applying memoize works
    cached_compute = memoize(memo.computeheavy)

    result1 = cached_compute(2, 10)
    result2 = cached_compute(2, 10)
    assert result1 == result2
    assert result1 == 1024


def test_memoize_different_args_different_results():
    """Different arguments should produce different cached results."""
    from importlib import reload
    import memo
    reload(memo)

    assert memo.fibonacci(5) == 5
    assert memo.fibonacci(6) == 8
    assert memo.fibonacci(5) == 5  # still cached


def test_memoize_on_recursive_function():
    """Memoization should dramatically speed up recursive functions."""
    import time
    from importlib import reload
    import memo
    reload(memo)
    fib = memo.fibonacci

    # Without memoization, fib(30) takes noticeable time
    # With memoization, it should be nearly instant
    start = time.time()
    result = fib(30)
    elapsed = time.time() - start

    # Should complete in well under a second (pure recursion would take ~seconds)
    assert elapsed < 0.5, f"fibonacci(30) took {elapsed:.3f}s — memoization may not be working"
    assert result == 832040
'''

test: "EvalSpec" = {
    "name": "implement-memoization",
    "task_type": "structured_process",
    "files": {
        "memo.py": MEMO_SRC,
        "test_memo.py": TEST_MEMO_SRC,
    },
    "run": "python3 -m pytest test_memo.py -v --tb=short 2>&1",
    "prompt": (
        "The `memoize` decorator in `memo.py` is a no-op stub — it just returns "
        "the original function without any caching.\n\n"
        "The test suite in `test_memo.py` is failing. Implement the `memoize` "
        "decorator so that it caches function results based on arguments:\n\n"
        "- When a memoized function is called, cache the result keyed by arguments\n"
        "- On subsequent calls with the same arguments, return the cached result\n"
        "- The cache should be a dict keyed by argument values (use `*args`/`**kwargs`)\n"
        "- Use only the Python standard library\n\n"
        "After implementing, run the tests to verify they all pass:\n"
        "  python3 -m pytest test_memo.py -v --tb=short\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has cache structure": check_has_cache,
        "caches based on args": check_has_args_caching,
        "is a decorator": check_is_decorator,
    },
}
