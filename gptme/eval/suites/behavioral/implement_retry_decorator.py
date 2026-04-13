"""Behavioral scenario: implement-retry-decorator."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "retry.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after implementing retry decorator."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_retry_function(ctx):
    """Should have a retry decorator or function."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            name_lower = node.name.lower()
            if "retry" in name_lower:
                return True
    return False


def check_accepts_exceptions_param(ctx):
    """Retry should accept configurable exception types."""
    content = _get_source(ctx)
    content_lower = content.lower()
    return (
        "exceptions" in content_lower
        or "retry_on" in content_lower
        or "catch" in content_lower
    )


def check_accepts_max_attempts(ctx):
    """Retry should accept max attempts parameter."""
    content = _get_source(ctx)
    content_lower = content.lower()
    return (
        "max_attempts" in content_lower
        or "max_retries" in content_lower
        or "attempts" in content_lower
        or "retries" in content_lower
        or "n_retries" in content_lower
    )


def check_has_backoff(ctx):
    """Retry should support exponential backoff."""
    content = _get_source(ctx)
    content_lower = content.lower()
    return (
        "backoff" in content_lower
        or "sleep" in content_lower
        or "delay" in content_lower
        or "exponential" in content_lower
        or "wait" in content_lower
    )


def check_wraps_function(ctx):
    """Retry decorator should use functools.wraps."""
    content = _get_source(ctx)
    return "wraps" in content or "functools" in content


RETRY_SRC = '''\
"""Retry utilities for unreliable operations."""
'''


TEST_RETRY_SRC = '''\
import time
from unittest.mock import patch, MagicMock

import pytest

from retry import retry


def test_retries_on_exception():
    """Should retry the function when specified exception is raised."""
    call_count = 0

    @retry(max_attempts=3, exceptions=(ValueError,))
    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("not yet")
        return "success"

    result = flaky()
    assert result == "success"
    assert call_count == 3


def test_raises_after_max_attempts():
    """Should raise the last exception after exhausting retries."""
    call_count = 0

    @retry(max_attempts=2, exceptions=(RuntimeError,))
    def always_fails():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("always")

    with pytest.raises(RuntimeError, match="always"):
        always_fails()

    assert call_count == 2


def test_passes_through_on_success():
    """Should return immediately on first successful call."""
    call_count = 0

    @retry(max_attempts=3, exceptions=(ValueError,))
    def works_first_try():
        nonlocal call_count
        call_count += 1
        return 42

    result = works_first_try()
    assert result == 42
    assert call_count == 1


def test_only_retries_specified_exceptions():
    """Should NOT retry exceptions not in the allowed list."""
    call_count = 0

    @retry(max_attempts=3, exceptions=(ValueError,))
    def raises_wrong_type():
        nonlocal call_count
        call_count += 1
        raise TypeError("wrong type")

    with pytest.raises(TypeError):
        raises_wrong_type()

    assert call_count == 1


def test_exponential_backoff():
    """Should wait with increasing delay between retries."""
    sleep_calls = []

    def mock_sleep(seconds):
        sleep_calls.append(seconds)

    call_count = 0

    @retry(max_attempts=4, exceptions=(ConnectionError,), base_delay=0.1)
    def flaky_connect():
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise ConnectionError("timeout")
        return "connected"

    with patch("retry.time.sleep", side_effect=mock_sleep):
        result = flaky_connect()

    assert result == "connected"
    assert call_count == 4
    # Should have slept 3 times (between attempts 1-2, 2-3, 3-4)
    assert len(sleep_calls) == 3
    # Delays should be increasing (exponential backoff)
    for i in range(1, len(sleep_calls)):
        assert sleep_calls[i] >= sleep_calls[i - 1], (
            f"Delay {sleep_calls[i]} should be >= {sleep_calls[i-1]}"
        )


def test_preserves_function_metadata():
    """Should preserve the original function's name and docstring."""
    @retry(max_attempts=3, exceptions=(Exception,))
    def my_function():
        """Original docstring."""
        return 1

    assert my_function.__name__ == "my_function"
    assert my_function.__doc__ == "Original docstring."
'''


# EvalSpec definition
test: "EvalSpec" = {
    "name": "implement-retry-decorator",
    "files": {
        "retry.py": RETRY_SRC,
        "test_retry.py": TEST_RETRY_SRC,
    },
    "run": "python3 -m pytest test_retry.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_retry.py` is failing because the `retry` decorator "
        "is not implemented in `retry.py`. The tests expect a `retry` decorator with:\n\n"
        "1. `max_attempts` — maximum number of tries (including the first call)\n"
        "2. `exceptions` — tuple of exception types to catch and retry on\n"
        "3. `base_delay` — initial delay in seconds for exponential backoff\n\n"
        "Behavior:\n"
        "- On first successful call, return immediately (no retries needed)\n"
        "- On specified exception, wait with exponential backoff and retry\n"
        "- On non-specified exception, raise immediately without retrying\n"
        "- After max_attempts exhausted, raise the last exception\n"
        "- Preserve function metadata using functools.wraps\n\n"
        "Signature: `retry(max_attempts: int = 3, exceptions: tuple = (Exception,), base_delay: float = 1.0)`\n\n"
        "Implement the decorator in `retry.py`, then run the tests.\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has retry function": check_has_retry_function,
        "accepts exceptions param": check_accepts_exceptions_param,
        "accepts max attempts": check_accepts_max_attempts,
        "has backoff": check_has_backoff,
        "wraps function": check_wraps_function,
    },
}
