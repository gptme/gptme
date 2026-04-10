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


def check_tests_pass(ctx):
    """All tests should pass after adding rate limiting."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_rate_limit_class(ctx):
    """Should have a rate limiter class or function."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if "rate" in name_lower or "limit" in name_lower or "bucket" in name_lower:
                return True
        if isinstance(node, ast.FunctionDef):
            name_lower = node.name.lower()
            if "rate" in name_lower or "limit" in name_lower:
                return True
    return False


def check_respects_rate_limit(ctx):
    """Should check rate limit before making requests."""
    content = _get_source(ctx)
    return "rate" in content.lower() and (
        "limit" in content.lower() or "wait" in content.lower()
    )


def check_handles_rate_limit_exceeded(ctx):
    """Should handle rate limit exceeded with proper exception or backoff."""
    content = _get_source(ctx)
    return "rate" in content.lower() and (
        "exceeded" in content.lower()
        or "429" in content
        or "backoff" in content.lower()
    )


def check_has_time_sleep(ctx):
    """Should use time.sleep for rate limiting."""
    content = _get_source(ctx)
    return "sleep" in content.lower() or "time" in content


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
import time
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
    call_times = []

    def mock_urlopen(*args, **kwargs):
        call_times.append(time.time())
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
        mock_resp.read.return_value = b'{"ok": True}'
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
        "all tests pass": check_tests_pass,
        "has rate limit class": check_has_rate_limit_class,
        "respects rate limit": check_respects_rate_limit,
        "handles rate limit exceeded": check_handles_rate_limit_exceeded,
        "has time sleep": check_has_time_sleep,
    },
}
