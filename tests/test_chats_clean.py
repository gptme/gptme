"""Tests for the chats clean command."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gptme.cli.util import _format_size, chats_clean
from gptme.logmanager import ConversationMeta
from gptme.tools.chats import find_empty_conversations

# --- Helpers ---


def _make_conv(
    id: str,
    messages: int,
    path: str = "",
    created: float = 1000.0,
    modified: float = 2000.0,
) -> ConversationMeta:
    return ConversationMeta(
        id=id,
        name=id,
        path=path or f"/tmp/fake/{id}/conversation.jsonl",
        created=created,
        modified=modified,
        messages=messages,
        branches=1,
        workspace="",
    )


# --- Unit tests for find_empty_conversations ---


def test_find_empty_no_conversations():
    """find_empty_conversations returns empty list when no conversations exist."""
    with (
        patch("gptme.logmanager.get_conversations", return_value=iter([])),
        patch("gptme.logmanager.get_user_conversations", return_value=iter([])),
    ):
        results = find_empty_conversations()
        assert results == []


def test_find_empty_filters_by_message_count(tmp_path: Path):
    """Only conversations with <= max_messages are returned."""
    for name in ["empty", "one-msg", "two-msg", "full"]:
        conv_dir = tmp_path / name
        conv_dir.mkdir()
        (conv_dir / "conversation.jsonl").write_text("x" * 100)

    convs = [
        _make_conv("empty", 0, path=str(tmp_path / "empty" / "conversation.jsonl")),
        _make_conv("one-msg", 1, path=str(tmp_path / "one-msg" / "conversation.jsonl")),
        _make_conv("two-msg", 2, path=str(tmp_path / "two-msg" / "conversation.jsonl")),
        _make_conv("full", 10, path=str(tmp_path / "full" / "conversation.jsonl")),
    ]

    with patch("gptme.logmanager.get_conversations", return_value=iter(convs)):
        results = find_empty_conversations(max_messages=1, include_test=True)
        ids = [r["conversation"].id for r in results]
        assert "empty" in ids
        assert "one-msg" in ids
        assert "two-msg" not in ids
        assert "full" not in ids

    with patch("gptme.logmanager.get_conversations", return_value=iter(convs)):
        results = find_empty_conversations(max_messages=2, include_test=True)
        ids = [r["conversation"].id for r in results]
        assert "two-msg" in ids
        assert "full" not in ids


def test_find_empty_calculates_size(tmp_path: Path):
    """Size calculation includes all files in conversation directory."""
    conv_dir = tmp_path / "test-conv"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("a" * 500)
    (conv_dir / "metadata.json").write_text("b" * 200)

    conv = _make_conv("test-conv", 0, path=str(conv_dir / "conversation.jsonl"))

    with patch("gptme.logmanager.get_conversations", return_value=iter([conv])):
        results = find_empty_conversations(include_test=True)
        assert len(results) == 1
        assert results[0]["size_bytes"] == 700


def test_find_empty_uses_user_conversations():
    """When include_test=False, uses get_user_conversations."""
    with patch(
        "gptme.logmanager.get_user_conversations", return_value=iter([])
    ) as mock:
        find_empty_conversations(include_test=False)
        mock.assert_called_once()


# --- Unit tests for _format_size ---


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (1073741824, "1.0 GB"),
    ],
)
def test_format_size(size_bytes: int, expected: str):
    assert _format_size(size_bytes) == expected


# --- CLI integration tests ---


@pytest.fixture
def mock_empty_convs(tmp_path: Path):
    """Create mock empty conversations with real directories."""
    conv_dir = tmp_path / "empty-chat"
    conv_dir.mkdir()
    (conv_dir / "conversation.jsonl").write_text("{}\n")

    conv = _make_conv("empty-chat", 0, path=str(conv_dir / "conversation.jsonl"))
    return [{"conversation": conv, "size_bytes": 3}]


def test_cli_clean_dry_run(mock_empty_convs):
    """Dry run shows conversations but doesn't delete."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=mock_empty_convs),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_clean, [])
        assert result.exit_code == 0
        assert "empty-chat" in result.output
        assert "Dry run" in result.output


def test_cli_clean_delete(mock_empty_convs):
    """--delete flag removes conversations."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=mock_empty_convs),
        patch("gptme.cli.util._ensure_tools"),
        patch("gptme.logmanager.delete_conversation", return_value=True) as mock_delete,
    ):
        result = runner.invoke(chats_clean, ["--delete"])
        assert result.exit_code == 0
        assert "Deleted 1" in result.output
        mock_delete.assert_called_once_with("empty-chat")


def test_cli_clean_no_results():
    """No empty conversations shows appropriate message."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=[]),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_clean, [])
        assert result.exit_code == 0
        assert "No empty conversations" in result.output


def test_cli_clean_no_results_json():
    """No empty conversations JSON output has consistent schema."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=[]),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_clean, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["found"] == 0
        assert data["deleted"] == 0
        assert data["freed_bytes"] == 0
        assert data["total_bytes"] == 0
        assert data["conversations"] == []


def test_cli_clean_json_output(mock_empty_convs):
    """--json flag outputs machine-readable JSON."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=mock_empty_convs),
        patch("gptme.cli.util._ensure_tools"),
    ):
        result = runner.invoke(chats_clean, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["found"] == 1
        assert data["deleted"] == 0
        assert data["conversations"][0]["id"] == "empty-chat"


def test_cli_clean_json_delete(mock_empty_convs):
    """--json --delete outputs correct deletion count."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=mock_empty_convs),
        patch("gptme.cli.util._ensure_tools"),
        patch("gptme.logmanager.delete_conversation", return_value=True),
    ):
        result = runner.invoke(chats_clean, ["--json", "--delete"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["found"] == 1
        assert data["deleted"] == 1


def test_cli_clean_max_messages():
    """--max-messages flag is passed through."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=[]) as mock_find,
        patch("gptme.cli.util._ensure_tools"),
    ):
        runner.invoke(chats_clean, ["-n", "3"])
        mock_find.assert_called_once_with(max_messages=3, include_test=False)


def test_cli_clean_include_test():
    """--include-test flag is passed through."""
    runner = CliRunner()
    with (
        patch("gptme.cli.util.find_empty_conversations", return_value=[]) as mock_find,
        patch("gptme.cli.util._ensure_tools"),
    ):
        runner.invoke(chats_clean, ["--include-test"])
        mock_find.assert_called_once_with(max_messages=1, include_test=True)
