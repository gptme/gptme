"""Tests for the RAG tool."""

from unittest.mock import patch

import pytest

from gptme.config import RagConfig
from gptme.message import Message
from gptme.tools.rag import _has_gptme_rag, _rag_context_hook


@pytest.mark.skipif(not _has_gptme_rag(), reason="RAG is not available")
def test_rag_context_hook():
    """Test that RAG context hook yields context messages."""
    messages = [
        Message("user", "Tell me about Python"),
        Message("assistant", "Python is a programming language"),
    ]

    # Call the hook
    context_msgs = list(_rag_context_hook(messages, workspace=None))

    # Should yield at least one context message
    assert len(context_msgs) >= 1
    assert all(msg.role == "system" for msg in context_msgs)


def test_rag_context_hook_no_rag():
    """Test that hook returns nothing when RAG is unavailable."""
    with patch("gptme.tools.rag._has_gptme_rag", return_value=False):
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        context_msgs = list(_rag_context_hook(messages, workspace=None))

        # Should yield nothing when RAG is not available
        assert len(context_msgs) == 0


def test_rag_context_hook_disabled():
    """Test hook when RAG is disabled in config."""
    with (
        patch("subprocess.run", return_value=type("Proc", (), {"returncode": 0})),
        patch("gptme.tools.rag.get_project_config") as mock_config,
    ):
        mock_config.return_value.rag = RagConfig(enabled=False)
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        context_msgs = list(_rag_context_hook(messages, workspace=None))

        # Should yield nothing when RAG is disabled
        assert len(context_msgs) == 0
