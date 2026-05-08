"""Behavioral scenario: add-deprecation-warning.

The agent must add a proper DeprecationWarning to the deprecated get_data() function
in api_client.py. The function already has a docstring noting it's deprecated, but
lacks the runtime warning that Python best practices require.

This tests knowledge of:
- Python warnings module (warnings.warn())
- DeprecationWarning category
- Proper API evolution practices
- Migration guidance in deprecation messages
"""

import ast
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gptme.eval.types import EvalSpec


def _get_client_source(ctx) -> str:
    content = ctx.files.get("api_client.py", "")
    return content if isinstance(content, str) else content.decode()


def _parse_source(ctx) -> ast.Module | None:
    source = _get_client_source(ctx)
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _get_function_def(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(module):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _is_warnings_warn_call(node: ast.AST) -> bool:
    """Return True if the AST node is a warnings.warn(...) call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "warn"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "warnings"
    )


def _has_warnings_warn_call_in_func(source: str, func_name: str) -> bool:
    """Check if warnings.warn() is called inside the named function body."""
    try:
        tree = ast.parse(source)
        func = _get_function_def(tree, func_name)
        if func is None:
            return False
        return any(_is_warnings_warn_call(node) for node in ast.walk(func))
    except SyntaxError:
        return False


def _get_warn_message_in_func(source: str, func_name: str) -> str:
    """Return the first-arg string literal of warnings.warn() inside the named function."""
    try:
        tree = ast.parse(source)
        func = _get_function_def(tree, func_name)
        if func is None:
            return ""
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Call)
                and _is_warnings_warn_call(node)
                and node.args
            ):
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant) and isinstance(
                    first_arg.value, str
                ):
                    return first_arg.value
        return ""
    except SyntaxError:
        return ""


def _has_deprecation_warning_category_in_func(source: str, func_name: str) -> bool:
    """Check if warnings.warn() with DeprecationWarning is called inside the named function."""
    try:
        tree = ast.parse(source)
        func = _get_function_def(tree, func_name)
        if func is None:
            return False
        for node in ast.walk(func):
            if not isinstance(node, ast.Call) or not _is_warnings_warn_call(node):
                continue
            # Check second positional arg or category keyword
            if len(node.args) >= 2:
                cat = node.args[1]
                if isinstance(cat, ast.Name) and cat.id == "DeprecationWarning":
                    return True
            for kw in node.keywords:
                if kw.arg == "category" and isinstance(kw.value, ast.Name):
                    if kw.value.id == "DeprecationWarning":
                        return True
        return False
    except SyntaxError:
        return False


def check_tests_pass(ctx):
    """All tests should pass after adding the deprecation warning."""
    return ctx.exit_code == 0 and "failed" not in ctx.stdout.lower()


def check_deprecation_warning_issued(ctx):
    """get_data() should issue a DeprecationWarning when called."""
    source = _get_client_source(ctx)
    return _has_warnings_warn_call_in_func(source, "get_data")


def check_deprecation_category(ctx):
    """DeprecationWarning should be the explicit category (not just UserWarning)."""
    source = _get_client_source(ctx)
    return _has_deprecation_warning_category_in_func(source, "get_data")


def check_docstring_updated(ctx):
    """The docstring should still note the deprecation and recommend fetch_metrics."""
    module = _parse_source(ctx)
    if module is None:
        return False
    func = _get_function_def(module, "get_data")
    if func is None:
        return False
    # Get the docstring
    docstring = ast.get_docstring(func) or ""
    return "deprecated" in docstring.lower() and "fetch_metrics" in docstring.lower()


def check_migration_guidance(ctx):
    """The deprecation warning message should provide migration guidance."""
    source = _get_client_source(ctx)
    # Check that the warnings.warn() message inside get_data() mentions the replacement
    msg = _get_warn_message_in_func(source, "get_data")
    if not msg:
        return False
    return "fetch_metrics" in msg or bool(
        re.search(r"use\s+\S+\s+instead", msg, re.IGNORECASE)
    )


test: "EvalSpec" = {
    "name": "add-deprecation-warning",
    "task_type": "structured_process",
    "files": {
        "api_client.py": '''\
"""API client module for the metrics service."""

import urllib.request
import json
from typing import Any


def fetch_metrics(endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """
    Fetch metrics from the given endpoint.

    Args:
        endpoint: The API endpoint URL
        params: Optional query parameters

    Returns:
        The JSON response as a dictionary
    """
    url = endpoint
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{endpoint}?{query}"

    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())


def get_summary(metrics: list[dict[str, Any]]) -> dict[str, float]:
    """
    Calculate summary statistics from a list of metric dictionaries.

    Args:
        metrics: List of metric dictionaries with 'value' keys

    Returns:
        Dictionary with min, max, and average values
    """
    if not metrics:
        return {"min": 0.0, "max": 0.0, "average": 0.0}

    values = [m["value"] for m in metrics if "value" in m]
    if not values:
        return {"min": 0.0, "max": 0.0, "average": 0.0}

    return {
        "min": min(values),
        "max": max(values),
        "average": sum(values) / len(values),
    }


# DEPRECATED: Use fetch_metrics instead
def get_data(url: str) -> dict[str, Any]:
    """
    Fetch JSON data from a URL.

    DEPRECATED: Use fetch_metrics() instead. This function will be removed
    in a future version.

    Args:
        url: The URL to fetch from

    Returns:
        The JSON response as a dictionary
    """
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read().decode())
''',
        "test_api_client.py": '''\
"""Tests for API client - validates deprecation warning behavior."""

import warnings
import pytest


def test_fetch_metrics_valid(monkeypatch):
    """Test that fetch_metrics correctly parses JSON responses."""
    import json
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"status": "ok", "data": [1, 2, 3]}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    import urllib.request
    original_urlopen = urllib.request.urlopen

    def mock_urlopen(url):
        return mock_response

    urllib.request.urlopen = mock_urlopen
    try:
        from api_client import fetch_metrics
        result = fetch_metrics("http://example.com/api")
        assert result["status"] == "ok"
        assert result["data"] == [1, 2, 3]
    finally:
        urllib.request.urlopen = original_urlopen


def test_get_summary_basic():
    """Test summary calculation."""
    from api_client import get_summary

    metrics = [{"value": 1}, {"value": 2}, {"value": 3}]
    result = get_summary(metrics)
    assert result["min"] == 1
    assert result["max"] == 3
    assert result["average"] == 2


def test_get_data_deprecated():
    """Test that get_data issues a DeprecationWarning."""
    import warnings
    import urllib.request
    from unittest.mock import MagicMock
    import json

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"key": "value"}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    original_urlopen = urllib.request.urlopen

    def mock_urlopen(url):
        return mock_response

    urllib.request.urlopen = mock_urlopen
    try:
        from api_client import get_data

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = get_data("http://example.com")

            # MUST have deprecation warning
            assert len(w) >= 1, "Expected DeprecationWarning to be issued"
            assert any(issubclass(warning.category, DeprecationWarning) for warning in w), \\
                f"Expected DeprecationWarning, got {[warning.category for warning in w]}"
            assert "fetch_metrics" in str(w[0].message).lower() or \\
                   "deprecated" in str(w[0].message).lower(), \\
                   f"Warning message should mention deprecation or fetch_metrics: {w[0].message}"

        assert result == {"key": "value"}
    finally:
        urllib.request.urlopen = original_urlopen


# END_ORIGINAL_TESTS


def test_fetch_metrics_with_params():
    """Test that fetch_metrics constructs query strings correctly."""
    import urllib.request
    from unittest.mock import MagicMock
    import json

    captured_url = []

    def capture_url(url):
        captured_url.append(url)
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"status": "ok"}).encode()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        return mock_response

    original_urlopen = urllib.request.urlopen
    urllib.request.urlopen = capture_url
    try:
        from api_client import fetch_metrics
        result = fetch_metrics("http://example.com/api", {"page": "1", "limit": "10"})
        assert len(captured_url) == 1
        assert "page=1" in captured_url[0] and "limit=10" in captured_url[0]
    finally:
        urllib.request.urlopen = original_urlopen
''',
    },
    "run": "python3 -m pytest test_api_client.py -v --tb=short 2>&1",
    "prompt": (
        "Add a `warnings.warn()` call to the deprecated `get_data()` function in "
        "`api_client.py`. The function's docstring already notes it's deprecated and "
        "recommends using `fetch_metrics()` instead, but it lacks the runtime warning "
        "that Python best practices require.\n\n"
        "Requirements:\n"
        "1. Add `import warnings` at the top of the file\n"
        "2. Add a `warnings.warn(...)` call at the start of `get_data()` that:\n"
        "   - Uses `DeprecationWarning` as the category\n"
        "   - Mentions `fetch_metrics()` as the replacement\n"
        "   - Uses `stacklevel=2` to point to the caller's line\n"
        "3. Keep the existing docstring unchanged\n"
        "4. All existing tests must continue to pass"
    ),
    "tools": ["shell", "save", "read"],
    "expect": {
        "tests pass": check_tests_pass,
        "deprecation warning issued": check_deprecation_warning_issued,
        "uses DeprecationWarning category": check_deprecation_category,
        "docstring notes deprecation": check_docstring_updated,
        "migration guidance provided": check_migration_guidance,
    },
}
