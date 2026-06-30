"""Tests for the /rag command."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.commands.base import CommandContext
from gptme.commands.rag import cmd_rag
from gptme.message import Message


@pytest.fixture()
def mock_manager():
    manager = MagicMock()
    manager.log = MagicMock()
    manager.log.messages = []
    manager.logdir = Path("/tmp/test-conversation")
    manager.logfile = Path("/tmp/test-conversation/conversation.jsonl")
    manager.name = "test-conversation"
    manager.workspace = Path("/tmp")
    return manager


def _make_ctx(full_args: str, manager: MagicMock) -> CommandContext:
    args = full_args.split() if full_args.strip() else []
    return CommandContext(args=args, full_args=full_args, manager=manager)


class TestRagCommand:
    def test_empty_query_prints_usage(self, mock_manager: MagicMock, capsys) -> None:
        ctx = _make_ctx("", mock_manager)
        list(cmd_rag(ctx))
        captured = capsys.readouterr()
        assert "Usage" in captured.out

    def test_missing_gptme_rag_prints_install_hint(
        self, mock_manager: MagicMock, capsys
    ) -> None:
        ctx = _make_ctx("pytest fixtures", mock_manager)
        with patch("gptme.commands.rag.shutil.which", return_value=None):
            list(cmd_rag(ctx))
        captured = capsys.readouterr()
        assert "gptme-rag" in captured.out
        assert "install" in captured.out.lower()

    def test_no_results_prints_message(self, mock_manager: MagicMock, capsys) -> None:
        ctx = _make_ctx("nonexistent topic XYZ123", mock_manager)
        with (
            patch("gptme.commands.rag.shutil.which", return_value="/usr/bin/gptme-rag"),
            patch("gptme.commands.rag.rag_search", return_value=""),
        ):
            messages = list(cmd_rag(ctx))
        captured = capsys.readouterr()
        assert messages == []
        assert "No relevant" in captured.out

    def test_results_injected_as_system_message(self, mock_manager: MagicMock) -> None:
        ctx = _make_ctx("pytest fixtures", mock_manager)
        fake_results = "Result 1: some snippet\nResult 2: another snippet"
        with (
            patch("gptme.commands.rag.shutil.which", return_value="/usr/bin/gptme-rag"),
            patch("gptme.commands.rag.rag_search", return_value=fake_results),
        ):
            messages = list(cmd_rag(ctx))
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, Message)
        assert msg.role == "system"
        assert "pytest fixtures" in msg.content
        assert fake_results in msg.content

    def test_search_exception_prints_error(
        self, mock_manager: MagicMock, capsys
    ) -> None:
        ctx = _make_ctx("pytest fixtures", mock_manager)
        with (
            patch("gptme.commands.rag.shutil.which", return_value="/usr/bin/gptme-rag"),
            patch(
                "gptme.commands.rag.rag_search",
                side_effect=RuntimeError("index empty"),
            ),
        ):
            messages = list(cmd_rag(ctx))
        captured = capsys.readouterr()
        assert messages == []
        assert "failed" in captured.out.lower()
        assert "index empty" in captured.out

    def test_rag_command_registered(self) -> None:
        # Importing commands.rag registers the command
        import gptme.commands.rag  # noqa: F401
        from gptme.commands.base import _command_registry

        assert "rag" in _command_registry

    def test_top_k_passed_to_search(self, mock_manager: MagicMock) -> None:
        ctx = _make_ctx("query text", mock_manager)
        with (
            patch("gptme.commands.rag.shutil.which", return_value="/usr/bin/gptme-rag"),
            patch(
                "gptme.commands.rag.rag_search", return_value="some result"
            ) as mock_search,
        ):
            list(cmd_rag(ctx))
        mock_search.assert_called_once_with("query text", top_k=3)
