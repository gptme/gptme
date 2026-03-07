"""Tests for the conversation stats feature (gptme-util chats stats)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import main
from gptme.logmanager import ConversationMeta
from gptme.tools.chats import _parse_since, conversation_stats

# --- _parse_since tests ---


def test_parse_since_none():
    assert _parse_since(None) is None


def test_parse_since_days():
    result = _parse_since("7d")
    assert result is not None
    expected = (datetime.now(tz=timezone.utc) - timedelta(days=7)).timestamp()
    # Allow 2 second tolerance
    assert abs(result - expected) < 2


def test_parse_since_date():
    result = _parse_since("2026-01-15")
    expected = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    assert result == expected


def test_parse_since_invalid():
    with pytest.raises(ValueError, match="Invalid --since format"):
        _parse_since("not-a-date")


def test_parse_since_zero_days():
    result = _parse_since("0d")
    assert result is not None
    now = datetime.now(tz=timezone.utc).timestamp()
    assert abs(result - now) < 2


# --- conversation_stats tests ---


def _make_conv(
    id: str,
    messages: int = 10,
    days_ago: int = 0,
    agent_name: str | None = None,
) -> ConversationMeta:
    """Helper to create test ConversationMeta objects."""
    now = datetime.now(tz=timezone.utc)
    ts = (now - timedelta(days=days_ago)).timestamp()
    return ConversationMeta(
        id=id,
        name=f"conv-{id}",
        path=f"/tmp/fake/{id}/conversation.jsonl",
        created=ts,
        modified=ts,
        messages=messages,
        branches=1,
        workspace="/tmp",
        agent_name=agent_name,
    )


@patch("gptme.logmanager.get_user_conversations")
def test_stats_basic(mock_get_convs, capsys):
    """Test basic stats output."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=20, days_ago=0, agent_name="Bob"),
            _make_conv("b", messages=10, days_ago=1),
            _make_conv("c", messages=30, days_ago=2, agent_name="Bob"),
        ]
    )

    conversation_stats()

    output = capsys.readouterr().out
    assert "Total conversations:  3" in output
    assert "Total messages:       60" in output
    assert "Avg messages/conv:    20.0" in output
    assert "Bob" in output
    assert "interactive" in output


@patch("gptme.logmanager.get_user_conversations")
def test_stats_json(mock_get_convs, capsys):
    """Test JSON output."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=20, days_ago=0, agent_name="Alice"),
            _make_conv("b", messages=10, days_ago=1),
        ]
    )

    conversation_stats(as_json=True)

    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["total_conversations"] == 2
    assert data["total_messages"] == 30
    assert data["avg_messages_per_conversation"] == 15.0
    assert "Alice" in data["by_agent"]
    assert "interactive" in data["by_agent"]


@patch("gptme.logmanager.get_user_conversations")
def test_stats_since_filter(mock_get_convs, capsys):
    """Test --since filtering stops at cutoff."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("recent", messages=5, days_ago=1),
            _make_conv("old", messages=5, days_ago=10),
        ]
    )

    conversation_stats(since="3d")

    output = capsys.readouterr().out
    assert "Total conversations:  1" in output


@patch("gptme.logmanager.get_user_conversations")
def test_stats_empty(mock_get_convs, capsys):
    """Test empty conversation list."""
    mock_get_convs.return_value = iter([])

    conversation_stats()

    output = capsys.readouterr().out
    assert "No conversations found." in output


@patch("gptme.logmanager.get_user_conversations")
def test_stats_since_empty(mock_get_convs, capsys):
    """Test empty result with --since filter."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("old", messages=5, days_ago=30),
        ]
    )

    conversation_stats(since="1d")

    output = capsys.readouterr().out
    assert "No conversations found since 1d." in output


@patch("gptme.logmanager.get_user_conversations")
def test_stats_median(mock_get_convs, capsys):
    """Test median calculation."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=100, days_ago=0),
            _make_conv("b", messages=10, days_ago=1),
            _make_conv("c", messages=5, days_ago=2),
        ]
    )

    conversation_stats()

    output = capsys.readouterr().out
    assert "Median messages/conv: 10" in output


@patch("gptme.logmanager.get_user_conversations")
def test_stats_median_even(mock_get_convs, capsys):
    """Test median calculation for even-length list (returns true median, not upper-middle)."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=20, days_ago=0),
            _make_conv("b", messages=10, days_ago=1),
        ]
    )

    conversation_stats()

    output = capsys.readouterr().out
    # True median of [10, 20] is 15.0, not 20 (upper-middle)
    assert "Median messages/conv: 15.0" in output


def test_stats_invalid_since():
    """Test that invalid --since value raises a friendly UsageError, not a traceback."""
    runner = CliRunner()
    result = runner.invoke(main, ["chats", "stats", "--since", "foo"])
    assert result.exit_code != 0
    # Should be a friendly usage error, not a Python traceback
    assert "Error" in result.output
    assert "Traceback" not in result.output


# --- CLI integration tests ---


@patch("gptme.logmanager.get_user_conversations")
def test_cli_chats_stats(mock_get_convs):
    """Test the CLI command via Click runner."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=15, days_ago=0, agent_name="Bob"),
            _make_conv("b", messages=25, days_ago=1),
        ]
    )

    runner = CliRunner()
    result = runner.invoke(main, ["chats", "stats", "--since", "7d"])
    assert result.exit_code == 0
    assert "Total conversations:  2" in result.output
    assert "Bob" in result.output


@patch("gptme.logmanager.get_user_conversations")
def test_cli_chats_stats_json(mock_get_convs):
    """Test the CLI command with --json flag."""
    mock_get_convs.return_value = iter(
        [
            _make_conv("a", messages=15, days_ago=0),
        ]
    )

    runner = CliRunner()
    result = runner.invoke(main, ["chats", "stats", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["total_conversations"] == 1


@patch("gptme.logmanager.get_user_conversations")
def test_stats_json_by_day_includes_zeros(mock_get_convs):
    """JSON by_day should include zero-count days (consistent with text histogram)."""
    import io
    from contextlib import redirect_stdout

    mock_get_convs.return_value = iter([_make_conv("a", messages=5, days_ago=0)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        conversation_stats(since=None, as_json=True)
    data = json.loads(buf.getvalue())
    # by_day should have hist_days (14) entries including zeros
    assert len(data["by_day"]) == 14
    # Today should have count 1
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    assert data["by_day"][today] == 1
    # Other days should be 0
    for day, count in data["by_day"].items():
        if day != today:
            assert count == 0, f"Expected 0 for {day}, got {count}"


@patch("gptme.logmanager.get_user_conversations")
def test_stats_hist_days_capped_at_365(mock_get_convs):
    """hist_days should be capped at 365 even for very old --since dates."""
    # --since 2020-01-01 would be ~2200 days — must be capped at 365.
    # Use a non-empty mock so conversation_stats doesn't exit early before
    # computing hist_days (empty mock hits "No conversations found" branch).
    mock_get_convs.return_value = iter([_make_conv("a", messages=5, days_ago=0)])

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        conversation_stats(since="2020-01-01", as_json=True)
    data = json.loads(buf.getvalue())
    # by_day must be capped at 365, not ~2200+ days
    assert len(data["by_day"]) == 365
