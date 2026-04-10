"""Behavioral scenario: retry-with-backoff."""

import ast
import re
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def check_retry_imported(ctx):
    """time module should be imported for sleep."""
    content = ctx.files.get("api_client.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "import time" in content or "from time import" in content


def check_retry_function_exists(ctx):
    """A retry-related function should exist."""
    content = ctx.files.get("api_client.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return "def retry" in content.lower() or "backoff" in content.lower()


def check_exponential_backoff(ctx):
    """Should implement exponential backoff (delay increases on each retry)."""
    content = ctx.files.get("api_client.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for a loop where sleep delay increases
    found_backoff = False
    for node in ast.walk(module):
        if isinstance(node, ast.For | ast.While):
            loop_code = ast.unparse(node) if hasattr(ast, "unparse") else str(node)
            if "sleep" in loop_code.lower() and any(
                op in loop_code for op in ["*", "**", "+="]
            ):
                found_backoff = True
    return found_backoff


def check_max_retries(ctx):
    """Should have a max retries limit to prevent infinite loops."""
    content = ctx.files.get("api_client.py", "")
    if isinstance(content, bytes):
        content = content.decode()
    return (
        "max_retries" in content.lower()
        or "max_attempts" in content.lower()
        or re.search(r"for .* in range\(\s*\d+\s*\)", content) is not None
    )


def check_tests_pass(ctx):
    """All tests should pass after adding retry logic."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


test: "EvalSpec" = {
    "name": "retry-with-backoff",
    "files": {
        "api_client.py": '''\
"""API client for fetching user data."""

import requests


def fetch_user(user_id: int) -> dict:
    """Fetch a user from the API. Retries on transient failures."""
    url = f"https://api.example.com/users/{user_id}"
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()
''',
        "test_api_client.py": '''\
import requests
import pytest
from unittest.mock import patch, MagicMock
from api_client import fetch_user


class FakeError(Exception):
    """Transient error that should trigger retry."""
    pass


def test_fetch_user_success(mocker):
    """Should return user data on successful request."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 1, "name": "Alice"}
    mocker.patch("requests.get", return_value=mock_resp)

    result = fetch_user(1)
    assert result["name"] == "Alice"
    assert requests.get.call_count == 1


def test_fetch_user_retries_on_transient_error(mocker):
    """Should retry and succeed after transient errors."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": 2, "name": "Bob"}

    # Fail twice, then succeed
    mocker.patch("requests.get", side_effect=[FakeError(), FakeError(), mock_resp])

    result = fetch_user(2)
    assert result["name"] == "Bob"
    # Should have retried: initial + 2 retries = 3 total calls
    assert requests.get.call_count == 3


def test_fetch_user_eventual_failure(mocker):
    """Should raise after max retries exceeded."""
    mocker.patch("requests.get", side_effect=FakeError())

    with pytest.raises(FakeError):
        fetch_user(3)
    # Should have retried max times before giving up
    assert requests.get.call_count > 1
''',
    },
    "run": "python3 -m pytest test_api_client.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_api_client.py` is failing because `fetch_user()` "
        "does not implement retry logic. Two tests fail:\n"
        "1. `test_fetch_user_retries_on_transient_error` — expects the function "
        "to retry on transient errors (FakeError) and eventually succeed.\n"
        "2. `test_fetch_user_eventual_failure` — expects the function to raise "
        "after exhausting retries.\n\n"
        "Implement exponential backoff retry logic in `api_client.py`:\n"
        "- Use `time.sleep()` between retries\n"
        "- Delay should increase exponentially (e.g., 0.1s, 0.2s, 0.4s...)\n"
        "- Set a max_retries limit (3-5 attempts)\n"
        "- Import the `time` module for sleep\n\n"
        "After implementing, run the tests to verify they all pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "time module imported": check_retry_imported,
        "retry function exists": check_retry_function_exists,
        "exponential backoff implemented": check_exponential_backoff,
        "max retries limit exists": check_max_retries,
        "all tests pass": check_tests_pass,
    },
}
