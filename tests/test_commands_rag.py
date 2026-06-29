"""Tests for the /rag slash command."""

from unittest.mock import MagicMock, patch

from gptme.commands.base import CommandContext
from gptme.commands.rag import cmd_rag
from gptme.message import Message


def _make_ctx(query: str) -> CommandContext:
    args = query.split() if query else []
    return CommandContext(args=args, full_args=query, manager=MagicMock())


def test_rag_cmd_no_query(capsys):
    """Empty /rag prints usage and yields nothing."""
    ctx = _make_ctx("")
    msgs = list(cmd_rag(ctx))
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert msgs == []


def test_rag_cmd_no_gptme_rag(capsys):
    """When gptme-rag is not installed, print a helpful message and yield nothing."""
    with patch("gptme.tools.rag._has_gptme_rag", return_value=False):
        ctx = _make_ctx("pytest fixtures")
        msgs = list(cmd_rag(ctx))
    out = capsys.readouterr().out
    assert "gptme-rag is not installed" in out
    assert msgs == []


def test_rag_cmd_injects_results():
    """When results exist, inject them as a user message."""
    fake_results = "### past-session-abc\nWe used pytest fixtures like this..."
    with (
        patch("gptme.tools.rag._has_gptme_rag", return_value=True),
        patch("gptme.tools.rag.rag_search", return_value=fake_results),
    ):
        ctx = _make_ctx("pytest fixtures")
        msgs = list(cmd_rag(ctx))

    assert len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, Message)
    assert msg.role == "user"
    assert "pytest fixtures" in msg.content
    assert fake_results in msg.content


def test_rag_cmd_no_results(capsys):
    """When RAG returns empty results, print a notice and yield nothing."""
    with (
        patch("gptme.tools.rag._has_gptme_rag", return_value=True),
        patch("gptme.tools.rag.rag_search", return_value="No results found."),
    ):
        ctx = _make_ctx("something obscure")
        msgs = list(cmd_rag(ctx))
    out = capsys.readouterr().out
    assert "No relevant" in out
    assert msgs == []


def test_rag_cmd_search_error(capsys):
    """RuntimeError from rag_search is caught and printed gracefully."""
    with (
        patch("gptme.tools.rag._has_gptme_rag", return_value=True),
        patch("gptme.tools.rag.rag_search", side_effect=RuntimeError("index missing")),
    ):
        ctx = _make_ctx("anything")
        msgs = list(cmd_rag(ctx))
    out = capsys.readouterr().out
    assert "RAG search failed" in out
    assert "index missing" in out
    assert msgs == []
