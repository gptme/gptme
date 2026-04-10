"""Behavioral scenario: retry-with-backoff."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "fetcher.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_tests_pass(ctx):
    """All tests should pass after adding retry logic."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_retry_loop(ctx):
    """Should use a retry loop (for/while with max attempts)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    has_loop = False
    for node in ast.walk(module):
        if isinstance(node, (ast.For, ast.While)):
            has_loop = True
    return has_loop and "retri" in content.lower()


def check_exponential_backoff(ctx):
    """Should use exponential backoff (sleep with increasing delay)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    has_sleep = "sleep" in content
    has_exponential = "**" in content or (
        "base" in content.lower() and "attempt" in content.lower()
    )
    return has_sleep and has_exponential


def check_catches_specific_exception(ctx):
    """Should catch a specific exception type (not bare except)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ExceptHandler):
            if node.type is not None:
                return True
    return False


def check_max_retries_limit(ctx):
    """Should have a maximum retry count to prevent infinite loops."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.For):
            if isinstance(node.iter, ast.Call):
                call = node.iter
                if isinstance(call.func, ast.Name) and call.func.id == "range":
                    return True
    return "max_retries" in content or "MAX_RETRIES" in content


FETCHER_SRC = '''\
"""HTTP fetcher for external API."""

import time


class FetchError(Exception):
    """Raised when fetching data fails."""


def fetch_data(url: str) -> dict:
    """Fetch JSON data from an external API.

    Args:
        url: The API endpoint URL.

    Returns:
        Parsed JSON response as a dict.
    """
    import json
    import urllib.request

    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        raise FetchError(f"Failed to fetch {url}")
'''

TEST_FETCHER_SRC = '''\
import time
from unittest.mock import patch, MagicMock

import pytest

from fetcher import fetch_data, FetchError


def test_fetch_success_on_first_try():
    """Should return data on first successful attempt."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "ok"}'
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = fetch_data("https://api.example.com/data")
        assert result == {"status": "ok"}


def test_fetch_retries_on_transient_failure():
    """Should retry when the server returns a transient error."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Connection reset by peer")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"retried": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("time.sleep"):
            result = fetch_data("https://api.example.com/data")
            assert result == {"retried": True}
            assert call_count == 3


def test_fetch_raises_after_max_retries():
    """Should raise FetchError after exhausting all retry attempts."""
    def side_effect(*args, **kwargs):
        raise ConnectionError("Connection refused")

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("time.sleep"):
            with pytest.raises(FetchError):
                fetch_data("https://api.example.com/data")


def test_retry_uses_exponential_backoff():
    """Should use increasing sleep intervals between retries."""
    sleep_calls = []

    def mock_sleep(seconds):
        sleep_calls.append(seconds)

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise ConnectionError("Temporary failure")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"done": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("time.sleep", side_effect=mock_sleep):
            result = fetch_data("https://api.example.com/data")
            assert result == {"done": True}

    # Sleep intervals should be strictly increasing (exponential backoff)
    assert len(sleep_calls) >= 2
    for i in range(1, len(sleep_calls)):
        assert sleep_calls[i] > sleep_calls[i - 1], (
            f"Sleep interval not increasing: {sleep_calls}"
        )


def test_no_retry_on_non_transient_error():
    """Should NOT retry on non-transient errors (e.g. 404 Not Found)."""
    from urllib.error import HTTPError

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise HTTPError(
            "https://api.example.com/missing", 404, "Not Found", {}, None
        )

    with patch("urllib.request.urlopen", side_effect=side_effect):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(FetchError):
                fetch_data("https://api.example.com/missing")
            # Should not retry on 4xx errors
            mock_sleep.assert_not_called()
'''

test: "EvalSpec" = {
    "name": "retry-with-backoff",
    "files": {
        "fetcher.py": FETCHER_SRC,
        "test_fetcher.py": TEST_FETCHER_SRC,
    },
    "run": "python3 -m pytest test_fetcher.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_fetcher.py` is failing because `fetch_data()` in "
        "`fetcher.py` does not implement retry logic. The tests expect:\n\n"
        "1. `test_fetch_retries_on_transient_failure` — should retry on ConnectionError "
        "and succeed on the 3rd attempt\n"
        "2. `test_fetch_raises_after_max_retries` — should raise FetchError after "
        "exhausting retries\n"
        "3. `test_retry_uses_exponential_backoff` — sleep intervals must be strictly "
        "increasing between retries\n"
        "4. `test_no_retry_on_non_transient_error` — should NOT retry on HTTP 404 "
        "errors (no sleep calls)\n\n"
        "Note: there is also a bug in the test file — `test_fetch_retries_on_transient_failure` "
        "and `test_retry_uses_exponential_backoff` have mock responses with `true` (JSON) "
        "instead of `True` (Python boolean). Fix those assertion values.\n\n"
        "Implement retry with exponential backoff in `fetch_data()`:\n"
        "- Retry only on transient errors (ConnectionError, TimeoutError), not on "
        "client errors like HTTP 404\n"
        "- Use exponential backoff with `time.sleep` (e.g., 1s, 2s, 4s, ...)\n"
        "- Set a reasonable maximum retry count (e.g., 5)\n"
        "- Wrap non-transient errors in FetchError without retrying\n"
        "After implementing, run the tests to verify they all pass."
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_tests_pass,
        "has retry loop": check_has_retry_loop,
        "exponential backoff": check_exponential_backoff,
        "catches specific exception": check_catches_specific_exception,
        "max retries limit": check_max_retries_limit,
    },
}
