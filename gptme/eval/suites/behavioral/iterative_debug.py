"""Behavioral scenario: iterative-debug."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_debug_tests_pass(ctx):
    """All tests should pass after the fix."""
    return ctx.exit_code == 0


def check_debug_no_syntax_error(ctx):
    """No SyntaxError or ImportError in test output."""
    lower = ctx.stdout.lower() + ctx.stderr.lower()
    return "syntaxerror" not in lower and "importerror" not in lower


def check_debug_fix_in_file(ctx):
    """calculator.py divide() should not silently swallow ZeroDivisionError."""
    content = ctx.files.get("calculator.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    # Detect the original bug by its unique pattern rather than by the fix.
    # The bug: catching ZeroDivisionError and returning None silently.
    # Valid fixes include: removing the try/except so Python raises naturally,
    # re-raising with `raise`, or guarding with `if b == 0: raise ...`.
    # All of these are accepted because they eliminate the buggy pattern.
    still_buggy = "except ZeroDivisionError" in content and "return None" in content
    return not still_buggy


test: "EvalSpec" = {
    "name": "iterative-debug",
    "task_type": "structured_process",
    "files": {
        "calculator.py": """\
\"\"\"Simple calculator module.\"\"\"


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return None  # BUG: should raise, not return None
""",
        "test_calculator.py": """\
import pytest
from calculator import add, subtract, multiply, divide


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract():
    assert subtract(10, 4) == 6


def test_multiply():
    assert multiply(3, 7) == 21


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5


def test_divide_by_zero():
    \"\"\"divide() must raise ZeroDivisionError, not return None.\"\"\"
    with pytest.raises(ZeroDivisionError):
        divide(5, 0)
""",
    },
    "run": "python3 -m pytest test_calculator.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite in test_calculator.py is failing. "
        "Run the tests to see which test fails, then fix the bug in "
        "calculator.py so that all tests pass. "
        "Do not modify the test file."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_debug_tests_pass,
        "no syntax/import errors": check_debug_no_syntax_error,
        "fix present in calculator.py": check_debug_fix_in_file,
    },
}
