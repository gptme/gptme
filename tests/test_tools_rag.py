"""Tests for the RAG tool."""

from unittest.mock import patch

import pytest

from gptme.config import RagConfig
from gptme.message import Message
from gptme.tools.rag import _has_gptme_rag, rag_enhance_messages


@pytest.mark.skipif(not _has_gptme_rag(), reason="RAG is not available")
def test_enhance_messages_with_rag():
    """Test that enhancement works when RAG is available."""
    messages = [
        Message("user", "Tell me about Python"),
        Message("assistant", "Python is a programming language"),
    ]

    enhanced = rag_enhance_messages(messages)

    # Enhanced messages should have extra RAG context msg
    assert len(enhanced) >= len(messages)


def test_enhance_messages_no_rag():
    """Test that enhancement works even without RAG available."""
    with patch("gptme.tools.rag._has_gptme_rag", return_value=False):
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        enhanced = rag_enhance_messages(messages)

        # Should be unchanged when RAG is not available
        assert len(enhanced) == len(messages)
        assert enhanced == messages


def test_enhance_messages_disabled():
    """Test message enhancement when RAG is disabled in config."""
    with (
        patch("subprocess.run", return_value=type("Proc", (), {"returncode": 0})),
        patch("gptme.tools.rag.get_project_config") as mock_config,
    ):
        mock_config.return_value.rag = RagConfig(enabled=False)
        messages = [
            Message("user", "Tell me about Python"),
            Message("assistant", "Python is a programming language"),
        ]

        enhanced = rag_enhance_messages(messages)

        # Should be unchanged when RAG is disabled
        assert len(enhanced) == len(messages)
        assert enhanced == messages
