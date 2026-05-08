"""Behavioral scenario: rate-limiting."""

import ast
from typing import TYPE_CHECKING

from ._common import parse_python_source

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_source(ctx, filename: str = "client.py") -> str:
    content = ctx.files.get(filename, "")
    if isinstance(content, bytes):
        content = content.decode()
    return content


def check_rate_limit_tests_pass(ctx):
    """All tests should pass after adding rate limiting."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_calls_time_sleep(ctx):
    """Should call time.sleep (not just import time) for backoff delays."""
    content = _get_source(ctx)
    # Starter only has 'import time'; agent must add an actual time.sleep() call
    return "time.sleep(" in content


def check_has_retry_loop(ctx):
    """Should have a loop for retrying requests (not just a single attempt)."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for a For or While loop inside the get() method body
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == "get":
            for child in ast.walk(node):
                if isinstance(child, ast.For | ast.While):
                    return True
    return False


def check_has_exponential_backoff(ctx):
    """Should implement exponential backoff (delay increases with each retry)."""
    content = _get_source(ctx)
    # Look for patterns like: delay * 2, 2 ** attempt, base_delay * (2 ** ...)
    return (
        "2 **" in content
        or "** 2" in content
        or "* 2" in content
        or "backoff" in content.lower()
        or "exponential" in content.lower()
    )


def check_only_retries_on_rate_limit(ctx):
    """Should only retry on 429 errors, not on other HTTP errors."""
    content = _get_source(ctx)
    # Must check for 429 before sleeping — ensures selective retry
    idx_429 = content.find("429")
    idx_sleep = content.find("time.sleep(")
    # 429 check must appear before or near the sleep call
    return idx_429 != -1 and idx_sleep != -1 and idx_429 < idx_sleep + 200


CLIENT_SRC = '''\
"""API client for external service."""

import urllib.request
import json
import time


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""


class APIClient:
    """Simple API client without rate limiting."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.example.com"

    def get(self, endpoint: str) -> dict:
        """Make a GET request to the API.

        Args:
            endpoint: The API endpoint path.

        Returns:
            Parsed JSON response as a dict.
        """
        url = f"{self.base_url}/{endpoint}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimitError("Rate limit exceeded")
            raise
'''

TEST_CLIENT_SRC = '''\
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from client import APIClient, RateLimitError


def test_successful_request():
    """Should return data on successful request."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"data": "success"}'
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        client = APIClient("test-key")
        result = client.get("/data")
        assert result == {"data": "success"}


def test_respects_rate_limit():
    """Should wait when rate limit is encountered."""
    client = APIClient("test-key")

    def mock_urlopen(*args, **kwargs):
        raise HTTPError("url", 429, "Too Many Requests", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(RateLimitError):
                client.get("/data")
            # Should have called sleep at least once before giving up
            assert mock_sleep.call_count >= 1


def test_rate_limit_backoff_increases():
    """Should increase wait time on repeated rate limits (backoff)."""
    client = APIClient("test-key")
    sleep_intervals = []

    def mock_sleep(seconds):
        sleep_intervals.append(seconds)

    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 4:
            raise HTTPError("url", 429, "Too Many Requests", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("time.sleep", side_effect=mock_sleep):
            result = client.get("/data")
            assert result == {"ok": True}

    # Sleep intervals should be increasing (exponential backoff)
    assert len(sleep_intervals) >= 2
    for i in range(1, len(sleep_intervals)):
        assert sleep_intervals[i] >= sleep_intervals[i - 1], (
            f"Backoff should increase or stay same: {sleep_intervals}"
        )


def test_raises_on_other_errors():
    """Should raise HTTPError for non-rate-limit errors."""
    client = APIClient("test-key")

    def mock_urlopen(*args, **kwargs):
        raise HTTPError("url", 500, "Internal Server Error", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(HTTPError):
                client.get("/data")
            # Should NOT retry on 5xx errors
            mock_sleep.assert_not_called()


def test_succeeds_after_rate_limit_resets():
    """Should succeed once rate limit resets (simulated by 3 failing then succeeding)."""
    client = APIClient("test-key")
    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise HTTPError("url", 429, "Too Many Requests", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"after_limit": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with patch("time.sleep"):
            result = client.get("/data")
            assert result == {"after_limit": True}
            assert call_count == 3
'''

test: "EvalSpec" = {
    "name": "rate-limiting",
    "task_type": "structured_process",
    "files": {
        "client.py": CLIENT_SRC,
        "test_client.py": TEST_CLIENT_SRC,
    },
    "run": "python3 -m pytest test_client.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_client.py` is failing because `APIClient` in "
        "`client.py` does not implement rate limiting. The tests expect:\n\n"
        "1. `test_respects_rate_limit` — should call time.sleep when rate limited\n"
        "2. `test_rate_limit_backoff_increases` — sleep intervals should increase "
        "or stay the same on repeated 429 errors\n"
        "3. `test_raises_on_other_errors` — should NOT retry on 5xx errors\n"
        "4. `test_succeeds_after_rate_limit_resets` — should retry and succeed once "
        "rate limit resets (after 2 failures, succeeds on 3rd call)\n\n"
        "Implement rate limiting with exponential backoff in `APIClient`:\n"
        "- Catch HTTP 429 (Too Many Requests) and raise RateLimitError\n"
        "- Use time.sleep with exponential backoff between retries\n"
        "- Only retry on rate limit errors, not on 5xx server errors\n"
        "- After implementing, run the tests to verify they all pass.\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_rate_limit_tests_pass,
        "calls time.sleep for backoff": check_calls_time_sleep,
        "has retry loop": check_has_retry_loop,
        "has exponential backoff": check_has_exponential_backoff,
        "only retries on 429": check_only_retries_on_rate_limit,
    },
}
