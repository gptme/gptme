"""Behavioral scenario: fix-off-by-one-loop."""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_tests_pass(ctx):
    """All tests should pass after fixing the off-by-one bug."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_sum_to_n_fixed(ctx):
    """sum_to_n() should use range(1, n+1) and return total (not total + n)."""
    content = ctx.files.get("math_utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Bug: range(1, n) produces 1 to n-1, not 1 to n
    # Also: "return total + n" adds n twice
    # Fix: range(1, n+1) and "return total"
    has_correct_range = "range(1, n + 1)" in content or "range(1,n+1)" in content
    has_simple_return = re.search(r"return total\s*$", content, re.MULTILINE)
    return has_correct_range and has_simple_return


def check_moving_average_preserved(ctx):
    """moving_average() should be unchanged — it has no bug."""
    content = ctx.files.get("math_utils.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def moving_average" in content and "window_size" in content


test: "EvalSpec" = {
    "name": "fix-off-by-one-loop",
    "files": {
        "math_utils.py": '''\
"""Math utility functions."""


def sum_to_n(n):
    """Return the sum of integers from 1 to n (inclusive).

    Uses a loop to sum values from 1 to n.
    """
    if n <= 0:
        return 0
    total = 0
    for i in range(1, n):  # BUG: off-by-one, iterates 1 to n-1
        total += i
    return total + n  # BUG: adds n again, but loop already misses it


def moving_average(values, window_size):
    """Return the moving average of a list with given window size.

    Uses a simple sliding window approach.
    """
    if window_size <= 0 or not values:
        return []
    result = []
    for i in range(len(values) - window_size + 1):
        window = values[i:i + window_size]
        result.append(sum(window) / window_size)
    return result
''',
        "test_math_utils.py": """\
import pytest
from math_utils import sum_to_n, moving_average


def test_sum_to_n_basic():
    assert sum_to_n(1) == 1
    assert sum_to_n(2) == 3  # 1 + 2
    assert sum_to_n(3) == 6  # 1 + 2 + 3
    assert sum_to_n(4) == 10  # 1 + 2 + 3 + 4
    assert sum_to_n(5) == 15  # 1 + 2 + 3 + 4 + 5


def test_sum_to_n_zero():
    assert sum_to_n(0) == 0


def test_sum_to_n_negative():
    assert sum_to_n(-1) == 0
    assert sum_to_n(-10) == 0


def test_sum_to_n_large():
    assert sum_to_n(10) == 55
    assert sum_to_n(100) == 5050


def test_moving_average_basic():
    assert moving_average([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]


def test_moving_average_small_window():
    assert moving_average([10, 20, 30], 2) == [15.0, 25.0]


def test_moving_average_equal_window():
    assert moving_average([1, 2, 3], 3) == [2.0]


def test_moving_average_empty():
    assert moving_average([], 2) == []


def test_moving_average_window_too_large():
    assert moving_average([1, 2, 3], 5) == []
""",
    },
    "run": "python3 -m pytest test_math_utils.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_math_utils.py` is failing. Run the tests to "
        "identify which tests fail, then fix the bugs in `math_utils.py`. "
        "The `sum_to_n()` function has TWO bugs: (1) the loop range is off-by-one, "
        "and (2) it incorrectly adds `n` again at the end. `moving_average()` is correct. "
        "After fixing, verify all tests pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_tests_pass,
        "sum_to_n bugs fixed": check_sum_to_n_fixed,
        "moving_average preserved": check_moving_average_preserved,
    },
}
