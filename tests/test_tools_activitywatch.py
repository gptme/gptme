"""Tests for ActivityWatch tool."""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest

from gptme.tools.activitywatch import (
    _check_aw_available,
    _format_duration,
    _get_aw_client,
    examples,
    get_today_summary,
    tool,
)


# Tests for _check_aw_available()
def test_check_aw_available_when_installed():
    """Test _check_aw_available when aw_client is installed."""
    # Mock successful import
    with patch("builtins.__import__", return_value=Mock()):
        assert _check_aw_available() is True


def test_check_aw_available_when_not_installed():
    """Test _check_aw_available when aw_client is not installed."""

    # Mock failed import
    def mock_import(name, *args):
        if name == "aw_client":
            raise ImportError("No module named 'aw_client'")
        return __import__(name, *args)

    with patch("builtins.__import__", side_effect=mock_import):
        assert _check_aw_available() is False


# Tests for _get_aw_client()
def test_get_aw_client_success():
    """Test _get_aw_client when aw_client is available."""
    mock_client_class = Mock()

    # Create a mock module with ActivityWatchClient
    mock_module = Mock()
    mock_module.ActivityWatchClient = mock_client_class

    with patch.dict("sys.modules", {"aw_client": mock_module}):
        result = _get_aw_client()
        assert result == mock_client_class


def test_get_aw_client_import_error():
    """Test _get_aw_client when aw_client is not installed."""
    # Simulate import failure by removing from sys.modules and blocking import
    import sys

    original_modules = sys.modules.copy()

    # Remove aw_client if it exists
    sys.modules.pop("aw_client", None)

    # Block future imports of aw_client
    sys.modules["aw_client"] = None  # type: ignore[assignment]

    try:
        with pytest.raises(ImportError) as exc_info:
            _get_aw_client()
        assert "aw-client not installed" in str(exc_info.value)
    finally:
        # Restore original sys.modules
        sys.modules.clear()
        sys.modules.update(original_modules)


# Tests for _format_duration()
def test_format_duration_hours():
    """Test formatting duration with hours."""
    td = timedelta(hours=3, minutes=45, seconds=22)
    assert _format_duration(td) == "3h 45m 22s"


def test_format_duration_minutes():
    """Test formatting duration with only minutes."""
    td = timedelta(minutes=45, seconds=22)
    assert _format_duration(td) == "45m 22s"


def test_format_duration_seconds():
    """Test formatting duration with only seconds."""
    td = timedelta(seconds=42)
    assert _format_duration(td) == "42s"


def test_format_duration_zero():
    """Test formatting zero duration."""
    td = timedelta()
    assert _format_duration(td) == "0s"


def test_format_duration_large():
    """Test formatting large duration."""
    td = timedelta(hours=25, minutes=30, seconds=45)
    assert _format_duration(td) == "25h 30m 45s"


# Tests for get_today_summary()
def test_get_today_summary_basic():
    """Test get_today_summary with basic activity data."""
    # Mock socket.gethostname inside the function
    with patch("socket.gethostname", return_value="test-host"):
        # Mock _get_aw_client to return a mock client class
        mock_client_class = Mock()
        mock_client = Mock()

        # Mock the client instance
        mock_client.get_buckets.return_value = {"aw-watcher-window_test-host": {}}

        # Mock events with realistic data
        mock_events = [
            {"duration": 3600, "data": {"app": "Code", "title": "main.py - VSCode"}},
            {
                "duration": 1800,
                "data": {"app": "Firefox", "title": "GitHub - Pull Request"},
            },
            {"duration": 1200, "data": {"app": "Code", "title": "test.py - VSCode"}},
        ]
        mock_client.get_events.return_value = mock_events

        # Make the client class return the mock client instance
        mock_client_class.return_value = mock_client

        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            result = get_today_summary()

        # Verify client was called correctly
        mock_client_class.assert_called_once_with(
            "gptme-activitywatch-tool", testing=False
        )
        mock_client.get_buckets.assert_called_once()
        mock_client.get_events.assert_called_once()

        # Verify output contains expected data
        assert "ActivityWatch Summary" in result
        assert "Total Active Time" in result
        assert "1h 50m 00s" in result  # Total time
        assert "Code" in result
        assert "Firefox" in result
        assert "Top Applications" in result
        assert "Top Window Titles" in result


def test_get_today_summary_with_explicit_hostname():
    """Test get_today_summary with explicit hostname parameter."""
    mock_client_class = Mock()
    mock_client = Mock()

    mock_client.get_buckets.return_value = {"aw-watcher-window_custom-host": {}}
    mock_client.get_events.return_value = []

    mock_client_class.return_value = mock_client

    with patch(
        "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
    ):
        get_today_summary(hostname="custom-host")

    # Should use custom hostname, not default
    assert mock_client.get_events.call_args[0][0] == "aw-watcher-window_custom-host"


def test_get_today_summary_connection_error():
    """Test get_today_summary when connection fails."""
    mock_client_class = Mock()
    mock_client_class.side_effect = Exception("Connection refused")

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            with pytest.raises(ConnectionError) as exc_info:
                get_today_summary()

    assert "Failed to connect to ActivityWatch server" in str(exc_info.value)
    assert "localhost:5600" in str(exc_info.value)


def test_get_today_summary_missing_bucket():
    """Test get_today_summary when bucket doesn't exist."""
    mock_client_class = Mock()
    mock_client = Mock()

    # Return buckets without the expected window bucket
    mock_client.get_buckets.return_value = {"aw-watcher-afk_test-host": {}}

    mock_client_class.return_value = mock_client

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            with pytest.raises(ValueError) as exc_info:
                get_today_summary()

    assert "Window watcher bucket not found" in str(exc_info.value)


def test_get_today_summary_empty_events():
    """Test get_today_summary with no events (no activity)."""
    mock_client_class = Mock()
    mock_client = Mock()

    mock_client.get_buckets.return_value = {"aw-watcher-window_test-host": {}}
    mock_client.get_events.return_value = []

    mock_client_class.return_value = mock_client

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            result = get_today_summary()

    assert "Total Active Time: 0s" in result
    assert "Top Applications" in result


def test_get_today_summary_long_titles():
    """Test get_today_summary with very long window titles."""
    mock_client_class = Mock()
    mock_client = Mock()

    mock_client.get_buckets.return_value = {"aw-watcher-window_test-host": {}}

    # Create event with very long title
    long_title = "A" * 100  # 100 characters
    mock_events = [
        {"duration": 3600, "data": {"app": "Code", "title": long_title}},
    ]
    mock_client.get_events.return_value = mock_events

    mock_client_class.return_value = mock_client

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            result = get_today_summary()

    # Verify title is truncated (max 60 chars + "...")
    assert "A" * 60 + "..." in result


def test_get_today_summary_missing_app_data():
    """Test get_today_summary when event data is missing app/title."""
    mock_client_class = Mock()
    mock_client = Mock()

    mock_client.get_buckets.return_value = {"aw-watcher-window_test-host": {}}

    # Events with missing data fields
    mock_events = [
        {"duration": 3600, "data": {}},  # Missing app and title
    ]
    mock_client.get_events.return_value = mock_events

    mock_client_class.return_value = mock_client

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            result = get_today_summary()

    # Should handle missing data with "Unknown"
    assert "Unknown" in result


def test_get_today_summary_aggregation():
    """Test that get_today_summary correctly aggregates time for same app/title."""
    mock_client_class = Mock()
    mock_client = Mock()

    mock_client.get_buckets.return_value = {"aw-watcher-window_test-host": {}}

    # Multiple events for same app
    mock_events = [
        {"duration": 1800, "data": {"app": "Code", "title": "main.py"}},
        {"duration": 1200, "data": {"app": "Code", "title": "test.py"}},
        {"duration": 600, "data": {"app": "Code", "title": "main.py"}},
    ]
    mock_client.get_events.return_value = mock_events

    mock_client_class.return_value = mock_client

    with patch("socket.gethostname", return_value="test-host"):
        with patch(
            "gptme.tools.activitywatch._get_aw_client", return_value=mock_client_class
        ):
            result = get_today_summary()

    # Total for Code should be 3600 (1h)
    assert "1h 00m 00s" in result or "1h 0m 0s" in result
    # main.py should show aggregated time of 2400 (40m)
    assert "40m" in result


# Tests for examples()
def test_examples_xml():
    """Test examples generation with XML format."""
    result = examples("xml")
    assert "ActivityWatch" in result
    assert "get_today_summary" in result
    assert "What have I been working on today?" in result


def test_examples_markdown():
    """Test examples generation with markdown format."""
    result = examples("markdown")
    assert "ActivityWatch" in result
    assert "get_today_summary" in result


# Tests for tool specification
def test_tool_spec():
    """Test tool specification is properly configured."""
    assert tool.name == "activitywatch"
    assert "productivity insights" in tool.desc.lower()
    assert "get_today_summary" in tool.instructions
    assert tool.functions is not None
    assert len(tool.functions) == 1
    assert tool.functions[0] == get_today_summary


def test_tool_available():
    """Test tool availability check."""
    assert callable(tool.available)
    with patch("gptme.tools.activitywatch._check_aw_available", return_value=True):
        assert tool.available() is True

    with patch("gptme.tools.activitywatch._check_aw_available", return_value=False):
        assert tool.available() is False
