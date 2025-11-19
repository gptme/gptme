"""Tests for the ActivityWatch tool."""

from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from gptme.message import Message
from gptme.tools.activitywatch import (
    ActivityWatchError,
    execute_activitywatch,
    format_summary,
    get_activity_summary,
    get_buckets,
    get_events,
    query_activity,
    query_time_spent,
)


# Mock data fixtures
@pytest.fixture
def mock_buckets():
    """Mock bucket data."""
    return {
        "aw-watcher-window_testhost": {
            "id": "aw-watcher-window_testhost",
            "type": "currentwindow",
            "hostname": "testhost",
        },
        "aw-watcher-afk_testhost": {
            "id": "aw-watcher-afk_testhost",
            "type": "afkstatus",
            "hostname": "testhost",
        },
    }


@pytest.fixture
def mock_events():
    """Mock event data."""
    return [
        {
            "timestamp": datetime.now().isoformat(),
            "duration": 3600,
            "data": {"app": "Code", "title": "test.py"},
        },
        {
            "timestamp": (datetime.now() - timedelta(hours=1)).isoformat(),
            "duration": 1800,
            "data": {"app": "Firefox", "title": "Documentation"},
        },
    ]


@pytest.fixture
def mock_query_result():
    """Mock query result data."""
    return {
        "apps": [
            {"name": "Code", "time_hours": 3.5},
            {"name": "Firefox", "time_hours": 2.0},
            {"name": "Terminal", "time_hours": 1.2},
        ],
        "total_time_hours": 6.7,
    }


# Test get_buckets
@patch("gptme.tools.activitywatch.requests.get")
def test_get_buckets_success(mock_get, mock_buckets):
    """Test successful bucket retrieval."""
    mock_response = Mock()
    mock_response.json.return_value = mock_buckets
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    buckets = get_buckets()
    assert len(buckets) == 2
    assert "aw-watcher-window_testhost" in buckets


@patch("gptme.tools.activitywatch.requests.get")
def test_get_buckets_connection_error(mock_get):
    """Test bucket retrieval with connection error."""
    import requests

    mock_get.side_effect = requests.exceptions.ConnectionError()

    with pytest.raises(ActivityWatchError) as exc_info:
        get_buckets()
    assert "Could not connect to ActivityWatch server" in str(exc_info.value)


@patch("gptme.tools.activitywatch.requests.get")
def test_get_buckets_request_error(mock_get):
    """Test bucket retrieval with request error."""
    import requests

    mock_get.side_effect = requests.exceptions.RequestException("Network error")

    with pytest.raises(ActivityWatchError) as exc_info:
        get_buckets()
    assert "Failed to get buckets" in str(exc_info.value)


# Test get_events
@patch("gptme.tools.activitywatch.requests.get")
def test_get_events_success(mock_get, mock_events):
    """Test successful event retrieval."""
    mock_response = Mock()
    mock_response.json.return_value = mock_events
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    start = datetime.now() - timedelta(hours=2)
    end = datetime.now()
    events = get_events("aw-watcher-window_testhost", start, end)

    assert len(events) == 2
    assert events[0]["data"]["app"] == "Code"


@patch("gptme.tools.activitywatch.requests.get")
def test_get_events_error(mock_get):
    """Test event retrieval with error."""
    import requests

    mock_get.side_effect = requests.exceptions.RequestException("Error")

    start = datetime.now() - timedelta(hours=2)
    end = datetime.now()

    with pytest.raises(ActivityWatchError) as exc_info:
        get_events("aw-watcher-window_testhost", start, end)
    assert "Failed to get events" in str(exc_info.value)


# Test query_activity
@patch("gptme.tools.activitywatch.get_events")
@patch("gptme.tools.activitywatch.get_buckets")
def test_query_activity_success(
    mock_buckets_fn, mock_events_fn, mock_buckets, mock_events
):
    """Test successful activity query."""
    mock_buckets_fn.return_value = mock_buckets
    mock_events_fn.return_value = mock_events

    result = query_activity(days=1)

    assert "apps" in result
    assert "total_time_hours" in result
    assert "time_range" in result
    assert len(result["apps"]) > 0


@patch("gptme.tools.activitywatch.get_buckets")
def test_query_activity_no_window_bucket(mock_buckets_fn):
    """Test query with no window bucket available."""
    mock_buckets_fn.return_value = {
        "aw-watcher-afk_testhost": {
            "id": "aw-watcher-afk_testhost",
            "type": "afkstatus",
        }
    }

    result = query_activity(days=1)
    assert "error" in result
    assert "No window watcher bucket found" in result["error"]


# Test format_summary
def test_format_summary():
    """Test activity summary formatting."""
    data = {
        "time_range": {"days": 1},
        "total_time_hours": 6.7,
        "apps": [
            {"name": "Code", "time_hours": 3.5, "percentage": 52.2},
            {"name": "Firefox", "time_hours": 2.0, "percentage": 29.9},
            {"name": "Terminal", "time_hours": 1.2, "percentage": 17.9},
        ],
    }
    summary = format_summary(data)

    assert "Activity Summary" in summary
    assert "Total Active Time" in summary
    assert "6.7" in summary
    assert "Code" in summary


def test_format_summary_empty():
    """Test formatting with no activity."""
    data = {
        "time_range": {"days": 1},
        "apps": [],
        "total_time_hours": 0.0,
    }
    summary = format_summary(data)

    assert "Activity Summary" in summary
    assert "0.0 hours" in summary
    assert "Top Applications:" in summary


# Test get_activity_summary
@patch("gptme.tools.activitywatch.query_activity")
@patch("gptme.tools.activitywatch.format_summary")
def test_get_activity_summary_success(mock_format, mock_query, mock_query_result):
    """Test successful activity summary retrieval."""
    mock_query.return_value = mock_query_result
    mock_format.return_value = "Activity summary text"

    result = get_activity_summary(days=7)

    assert result == "Activity summary text"
    mock_query.assert_called_once_with(days=7)


@patch("gptme.tools.activitywatch.query_activity")
def test_get_activity_summary_error(mock_query):
    """Test activity summary with error."""
    mock_query.side_effect = Exception("Query failed")

    result = get_activity_summary(days=1)

    assert "Error getting activity summary" in result
    assert "Query failed" in result


# Test query_time_spent
@patch("gptme.tools.activitywatch.query_activity")
def test_query_time_spent_found(mock_query, mock_query_result):
    """Test querying time spent in specific app."""
    mock_query.return_value = mock_query_result

    result = query_time_spent("code", days=7)

    assert result == 3.5
    mock_query.assert_called_once_with(days=7)


@patch("gptme.tools.activitywatch.query_activity")
def test_query_time_spent_not_found(mock_query, mock_query_result):
    """Test querying time for non-existent app."""
    mock_query.return_value = mock_query_result

    result = query_time_spent("nonexistent", days=7)

    assert result == 0.0


@patch("gptme.tools.activitywatch.query_activity")
def test_query_time_spent_error(mock_query):
    """Test time query with error."""
    mock_query.side_effect = Exception("Query failed")

    with pytest.raises(ActivityWatchError) as exc_info:
        query_time_spent("code", days=1)
    assert "Failed to query time for 'code'" in str(exc_info.value)


# Test execute_activitywatch
@patch("gptme.tools.activitywatch.query_activity")
@patch("gptme.tools.activitywatch.format_summary")
def test_execute_activitywatch_summary(mock_format, mock_query):
    """Test tool execution with summary command."""
    mock_query.return_value = {
        "time_range": {"days": 7},
        "apps": [],
        "total_time_hours": 0.0,
    }
    mock_format.return_value = "Activity summary"

    msg = execute_activitywatch("summary 7", ["summary", "7"], None, lambda _: True)

    assert isinstance(msg, Message)
    assert msg.role == "system"
    assert "Activity summary" in msg.content


@patch("gptme.tools.activitywatch.get_buckets")
def test_execute_activitywatch_buckets(mock_buckets_fn, mock_buckets):
    """Test tool execution with buckets command."""
    mock_buckets_fn.return_value = mock_buckets

    msg = execute_activitywatch("buckets", ["buckets"], None, lambda _: True)

    assert isinstance(msg, Message)
    assert "aw-watcher-window_testhost" in msg.content


def test_execute_activitywatch_invalid_days():
    """Test tool execution with invalid days parameter."""
    msg = execute_activitywatch(
        "summary invalid", ["summary", "invalid"], None, lambda _: True
    )

    assert isinstance(msg, Message)
    assert "Invalid days parameter" in msg.content


def test_execute_activitywatch_unknown_command():
    """Test tool execution with unknown command."""
    msg = execute_activitywatch("unknown", ["unknown"], None, lambda _: True)

    assert isinstance(msg, Message)
    assert "Unknown command" in msg.content


@patch("gptme.tools.activitywatch.query_activity")
def test_execute_activitywatch_error(mock_query):
    """Test tool execution with ActivityWatch error."""
    mock_query.side_effect = ActivityWatchError("Server not running")

    msg = execute_activitywatch("summary", ["summary"], None, lambda _: True)

    assert isinstance(msg, Message)
    assert "ActivityWatch error" in msg.content
    assert "Server not running" in msg.content
