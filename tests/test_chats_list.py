"""Tests for the chats list command (--since and --json flags)."""

import json
import time
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import _parse_since, chats_list
from gptme.logmanager import ConversationMeta

# --- Helpers ---


def _make_conv(
    id: str,
    messages: int = 5,
    created: float = 1000.0,
    modified: float = 2000.0,
) -> ConversationMeta:
    return ConversationMeta(
        id=id,
        name=id,
        path=f"/tmp/fake/{id}/conversation.jsonl",
        created=created,
        modified=modified,
        messages=messages,
        branches=1,
        workspace="/tmp/workspace",
    )


# --- Unit tests for _parse_since ---


def test_parse_since_hours():
    """Parse hour durations."""
    now = time.time()
    result = _parse_since("1h")
    assert abs(result - (now - 3600)) < 2


def test_parse_since_days():
    """Parse day durations."""
    now = time.time()
    result = _parse_since("3d")
    assert abs(result - (now - 3 * 86400)) < 2


def test_parse_since_weeks():
    """Parse week durations."""
    now = time.time()
    result = _parse_since("2w")
    assert abs(result - (now - 2 * 604800)) < 2


def test_parse_since_invalid():
    """Invalid duration raises BadParameter."""
    from click import BadParameter

    with pytest.raises(BadParameter, match="Invalid duration"):
        _parse_since("invalid")


def test_parse_since_invalid_unit():
    """Unknown unit raises BadParameter."""
    from click import BadParameter

    with pytest.raises(BadParameter, match="Invalid duration"):
        _parse_since("5x")


# --- CLI integration tests ---


@pytest.fixture
def mock_conversations():
    """Create mock conversations with varying timestamps."""
    now = time.time()
    return [
        _make_conv("recent", modified=now - 1800),  # 30 min ago
        _make_conv("today", modified=now - 7200),  # 2 hours ago
        _make_conv("yesterday", modified=now - 90000),  # ~25 hours ago
        _make_conv("old", modified=now - 604800),  # 1 week ago
    ]


def test_cli_list_limit(mock_conversations):
    """--limit restricts number of results."""
    runner = CliRunner()
    with (
        patch(
            "gptme.logmanager.list_conversations", return_value=mock_conversations[:2]
        ),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["-n", "2", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2


def test_cli_list_json(mock_conversations):
    """--json flag outputs valid JSON."""
    runner = CliRunner()
    with (
        patch(
            "gptme.logmanager.list_conversations", return_value=mock_conversations[:2]
        ),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["-n", "2", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["id"] == "recent"
        assert "created" in data[0]
        assert "modified" in data[0]
        assert "messages" in data[0]
        assert data[0]["created"].endswith("Z")


def test_cli_list_json_empty():
    """--json with no conversations returns empty array."""
    runner = CliRunner()
    with (
        patch("gptme.logmanager.list_conversations", return_value=[]),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []


def test_cli_list_since(mock_conversations):
    """--since filters conversations by modified time."""
    runner = CliRunner()
    with (
        patch("gptme.logmanager.list_conversations", return_value=mock_conversations),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["--since", "3h", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [c["id"] for c in data]
        assert "recent" in ids
        assert "today" in ids
        assert "yesterday" not in ids
        assert "old" not in ids


def test_cli_list_since_1d(mock_conversations):
    """--since 1d shows only conversations from last 24 hours."""
    runner = CliRunner()
    with (
        patch("gptme.logmanager.list_conversations", return_value=mock_conversations),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["--since", "1d", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        ids = [c["id"] for c in data]
        assert "recent" in ids
        assert "today" in ids
        assert "yesterday" not in ids


def test_cli_list_since_invalid():
    """Invalid --since value shows error."""
    runner = CliRunner()
    with patch("gptme.cli.util._ensure_tools"):
        result = runner.invoke(chats_list, ["--since", "abc"])
        assert result.exit_code != 0
        assert "Invalid duration" in result.output


def test_cli_list_json_schema(mock_conversations):
    """JSON output has expected schema fields."""
    runner = CliRunner()
    with (
        patch(
            "gptme.logmanager.list_conversations",
            return_value=[mock_conversations[0]],
        ),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_list, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        entry = data[0]
        expected_keys = {
            "id",
            "name",
            "created",
            "modified",
            "messages",
            "branches",
            "workspace",
        }
        assert set(entry.keys()) == expected_keys
