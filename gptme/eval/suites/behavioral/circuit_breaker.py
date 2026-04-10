"""Behavioral scenario: circuit-breaker."""

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


def check_circuit_breaker_tests_pass(ctx):
    """All tests should pass after adding circuit breaker."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_has_circuit_breaker(ctx):
    """Should have a circuit breaker class or mechanism."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    for node in ast.walk(module):
        if isinstance(node, ast.ClassDef):
            name_lower = node.name.lower()
            if "circuit" in name_lower and "breaker" in name_lower:
                return True
        if isinstance(node, ast.FunctionDef):
            name_lower = node.name.lower()
            if "circuit" in name_lower or "breaker" in name_lower:
                return True
    return False


def check_has_failure_tracking(ctx):
    """Should track failures to determine circuit state."""
    content = _get_source(ctx)
    module = parse_python_source(content)
    if module is None:
        return False
    # Look for failure count tracking as an attribute (e.g. self.failure_count)
    # Avoid false-positives from the starter's CircuitOpenError class name
    for node in ast.walk(module):
        if isinstance(node, ast.Attribute):
            if "failure" in node.attr.lower():
                return True
    # Fallback: plain variable name containing "failure" (e.g. failure_count = 0)
    return "failure_count" in content.lower() or "failures" in content.lower()


def check_has_success_reset(ctx):
    """Should reset circuit on successful call."""
    content = _get_source(ctx)
    return (
        "reset" in content.lower()
        or "success" in content.lower()
        or "failure_count = 0" in content.lower()
        or "failures = 0" in content.lower()
    )


def check_has_open_state(ctx):
    """Should have open/closed circuit states."""
    content = _get_source(ctx)
    content_lower = content.lower()
    has_open = "open" in content_lower or "open_state" in content_lower
    has_close = "close" in content_lower or "closed" in content_lower
    return has_open and has_close


CLIENT_SRC = '''\
"""API client with circuit breaker pattern."""

import urllib.request
import json
import time


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""


class APIClient:
    """Simple API client without circuit breaker."""

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
            raise
'''

TEST_CLIENT_SRC = '''\
import time
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import pytest

from client import APIClient, CircuitOpenError


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


def test_circuit_opens_after_failures():
    """Circuit should open after consecutive failures."""
    client = APIClient("test-key")

    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise HTTPError("url", 503, "Service Unavailable", {}, None)

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        for _ in range(5):
            try:
                client.get("/data")
            except (HTTPError, CircuitOpenError):
                pass

        # After 5 consecutive failures (threshold is 3), circuit should be open
        with pytest.raises(CircuitOpenError):
            client.get("/data")


def test_circuit_resets_on_success():
    """Circuit should reset failure count on successful request."""
    client = APIClient("test-key")

    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2 or call_count == 4:
            raise HTTPError("url", 503, "Service Unavailable", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # First two calls fail
        for _ in range(2):
            try:
                client.get("/data")
            except HTTPError:
                pass

        # Third call succeeds, resets failure count
        result = client.get("/data")
        assert result == {"ok": True}

        # Fourth call fails — but circuit was just reset, so it stays closed
        with pytest.raises(HTTPError):
            client.get("/data")

        # Fifth call succeeds — circuit still closed (only 1 failure since reset)
        result = client.get("/data")
        assert result == {"ok": True}


def test_raises_on_circuit_open():
    """Should raise CircuitOpenError when circuit is open."""
    client = APIClient("test-key")

    call_count = 0

    def mock_urlopen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise HTTPError("url", 503, "Service Unavailable", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # Trigger circuit open
        for _ in range(3):
            try:
                client.get("/data")
            except HTTPError:
                pass

        # Now circuit is open
        with pytest.raises(CircuitOpenError):
            client.get("/data")
'''

test: "EvalSpec" = {
    "name": "circuit-breaker",
    "files": {
        "client.py": CLIENT_SRC,
        "test_client.py": TEST_CLIENT_SRC,
    },
    "run": "python3 -m pytest test_client.py -v --tb=short 2>&1",
    "prompt": (
        "The test suite `test_client.py` is failing because `APIClient` in "
        "`client.py` does not implement the circuit breaker pattern. The tests expect:\n\n"
        "1. `test_circuit_opens_after_failures` — circuit should open after 3+ consecutive failures\n"
        "2. `test_circuit_resets_on_success` — failure count should reset on successful request\n"
        "3. `test_raises_on_circuit_open` — should raise CircuitOpenError when circuit is open\n\n"
        "Implement the circuit breaker pattern in `APIClient`:\n"
        "- Track consecutive failures (server errors: 5xx)\n"
        "- After 3 consecutive failures, open the circuit and raise CircuitOpenError\n"
        "- On successful request, reset the failure count\n"
        "- When circuit is open, fail fast without making requests\n"
        "- After implementing, run the tests to verify they all pass.\n"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "all tests pass": check_circuit_breaker_tests_pass,
        "has circuit breaker": check_has_circuit_breaker,
        "has failure tracking": check_has_failure_tracking,
        "has success reset": check_has_success_reset,
        "has open state": check_has_open_state,
    },
}
