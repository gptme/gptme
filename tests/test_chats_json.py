"""Tests for --json output on chats list and chats search commands."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gptme.cli.cmd_chats import _conv_to_dict
from gptme.cli.util import main
from gptme.logmanager import ConversationMeta
from gptme.message import Message


def _make_conv(
    id: str,
    messages: int = 10,
    days_ago: int = 0,
    agent_name: str | None = None,
    model: str | None = "claude-sonnet-4-6",
    cost: float = 0.05,
    input_tokens: int = 1000,
    output_tokens: int = 500,
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
        model=model,
        total_cost=cost,
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
    )


# --- _conv_to_dict tests ---


class TestConvToDict:
    def test_basic_fields(self):
        conv = _make_conv("test-1", messages=5, model="gpt-4o", cost=0.123)
        d = _conv_to_dict(conv)
        assert d["id"] == "test-1"
        assert d["name"] == "conv-test-1"
        assert d["messages"] == 5
        assert d["model"] == "gpt-4o"
        assert d["total_cost"] == 0.123

    def test_timestamps_are_iso(self):
        conv = _make_conv("test-2")
        d = _conv_to_dict(conv)
        # Should be valid ISO format
        datetime.fromisoformat(d["created"])
        datetime.fromisoformat(d["modified"])

    def test_none_fields(self):
        conv = _make_conv("test-3", agent_name=None, model=None)
        d = _conv_to_dict(conv)
        assert d["agent_name"] is None
        assert d["model"] is None

    def test_cost_rounding(self):
        conv = _make_conv("test-4", cost=0.123456789)
        d = _conv_to_dict(conv)
        assert d["total_cost"] == 0.1235

    def test_json_serializable(self):
        conv = _make_conv("test-5")
        d = _conv_to_dict(conv)
        # Must not raise
        json.dumps(d)


# --- chats list --json tests ---


class TestChatsListJson:
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_list_json_output(self, _mock_tools, mock_list_convs):
        """Test that --json produces valid JSON array."""
        mock_list_convs.return_value = [
            _make_conv("a", messages=20, agent_name="Bob"),
            _make_conv("b", messages=10),
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "list", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["id"] == "a"
        assert data[0]["messages"] == 20
        assert data[0]["agent_name"] == "Bob"
        assert data[1]["id"] == "b"

    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_list_json_empty(self, _mock_tools, mock_list_convs):
        """Test --json with no conversations."""
        mock_list_convs.return_value = []
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_list_json_respects_limit(self, _mock_tools, mock_list_convs):
        """Test that --json respects -n limit."""
        mock_list_convs.return_value = [_make_conv("a")]
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "list", "--json", "-n", "5"])
        assert result.exit_code == 0
        mock_list_convs.assert_called_once_with(5)

    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_list_json_includes_token_fields(self, _mock_tools, mock_list_convs):
        """Test that JSON output includes cost and token fields."""
        mock_list_convs.return_value = [
            _make_conv("a", cost=1.23, input_tokens=5000, output_tokens=2000)
        ]
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["total_cost"] == 1.23
        assert data[0]["total_input_tokens"] == 5000
        assert data[0]["total_output_tokens"] == 2000


# --- chats search --json tests ---


def _make_log_manager_with_messages(messages: list[Message]):
    """Create a mock LogManager with given messages."""
    mock = MagicMock()
    mock.log = messages
    return mock


class TestChatsSearchJson:
    @patch("gptme.logmanager.LogManager")
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_search_json_output(self, _mock_tools, mock_list_convs, mock_lm):
        """Test that search --json produces valid JSON with matches."""
        conv = _make_conv("a", messages=5)
        mock_list_convs.return_value = [conv]
        mock_lm.load.return_value = _make_log_manager_with_messages(
            [
                Message("user", "hello world"),
                Message("assistant", "hello back"),
                Message("user", "tell me about python"),
            ]
        )
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "search", "hello", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "a"
        assert data[0]["matches"] == 2
        assert len(data[0]["snippets"]) == 2

    @patch("gptme.logmanager.LogManager")
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_search_json_no_results(self, _mock_tools, mock_list_convs, mock_lm):
        """Test search --json with no matches."""
        conv = _make_conv("a", messages=5)
        mock_list_convs.return_value = [conv]
        mock_lm.load.return_value = _make_log_manager_with_messages(
            [Message("user", "hello world")]
        )
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "search", "nonexistent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

    @patch("gptme.logmanager.LogManager")
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_search_json_snippets_truncated(
        self, _mock_tools, mock_list_convs, mock_lm
    ):
        """Test that search snippets are truncated to 200 chars."""
        long_content = "x" * 500
        conv = _make_conv("a")
        mock_list_convs.return_value = [conv]
        mock_lm.load.return_value = _make_log_manager_with_messages(
            [Message("user", long_content)]
        )
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "search", "xxx", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert len(data[0]["snippets"][0]["content"]) == 200

    @patch("gptme.logmanager.LogManager")
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_search_json_max_3_snippets(self, _mock_tools, mock_list_convs, mock_lm):
        """Test that at most 3 snippet previews are returned per conversation."""
        conv = _make_conv("a")
        mock_list_convs.return_value = [conv]
        mock_lm.load.return_value = _make_log_manager_with_messages(
            [Message("user", f"match {i}") for i in range(10)]
        )
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "search", "match", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["matches"] == 10
        assert len(data[0]["snippets"]) == 3

    @patch("gptme.logmanager.LogManager")
    @patch("gptme.logmanager.list_conversations")
    @patch("gptme.tools.get_tools", return_value=[MagicMock()])
    def test_search_json_snippet_fields(self, _mock_tools, mock_list_convs, mock_lm):
        """Test that snippets contain expected fields."""
        conv = _make_conv("a")
        mock_list_convs.return_value = [conv]
        mock_lm.load.return_value = _make_log_manager_with_messages(
            [Message("user", "search term here")]
        )
        runner = CliRunner()
        result = runner.invoke(main, ["chats", "search", "search", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        snippet = data[0]["snippets"][0]
        assert "index" in snippet
        assert "role" in snippet
        assert "content" in snippet
        assert snippet["role"] == "user"
