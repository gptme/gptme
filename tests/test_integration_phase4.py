"""Phase 4 integration tests for context selector with real conversations.

Tests file selection with real Message objects across strategies
(rule-based, LLM-based, hybrid).
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gptme.context.selector import (
    FileSelectorConfig,
    select_relevant_files,
)
from gptme.message import Message

_has_llm_api_key = bool(
    os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
)
_needs_llm = pytest.mark.skipif(
    not _has_llm_api_key, reason="No LLM API key available (OPENAI or ANTHROPIC)"
)


@pytest.fixture
def conversation_about_git():
    """Create a realistic conversation about git workflows."""
    return [
        Message(
            role="user",
            content="I need help with git workflow",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="assistant",
            content="I can help with git. What specific aspect?",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="user",
            content="How do I create a branch and push it correctly?",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="assistant",
            content="Let me show you the git worktree workflow",
            timestamp=datetime.now(tz=timezone.utc),
        ),
    ]


@pytest.fixture
def conversation_mixed_topics():
    """Create a realistic conversation with multiple topics."""
    return [
        Message(
            role="user",
            content="I need to commit my changes and then run some tests",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="assistant",
            content="First let's handle the git commit",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="user",
            content="Also, how do I quote paths with spaces in shell?",
            timestamp=datetime.now(tz=timezone.utc),
        ),
        Message(
            role="assistant",
            content="Use quotes around the path",
            timestamp=datetime.now(tz=timezone.utc),
        ),
    ]


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        # Create some test files
        (workspace / "main.py").write_text("print('hello')\n")
        (workspace / "test_main.py").write_text("def test_hello(): pass\n")
        (workspace / "README.md").write_text("# Test Project\n")
        (workspace / "config.yaml").write_text("key: value\n")
        yield workspace


class TestFileSelectionIntegration:
    """Test file selection with real conversations."""

    def test_rule_based_file_selection(self, conversation_about_git, temp_workspace):
        """Test rule-based file selection."""
        config = FileSelectorConfig(strategy="rule")

        # Get files (async even for rule-based)
        result = select_relevant_files(
            conversation_about_git,
            temp_workspace,
            max_files=5,
            use_selector=False,
            config=config,
        )

        # Should return list of files
        assert isinstance(result, list)
        # Files should exist
        assert all(isinstance(f, Path) for f in result)

    @_needs_llm
    @pytest.mark.slow
    @pytest.mark.xfail(
        reason="Needs more realistic file fixtures with substantial content"
    )
    def test_llm_based_file_selection(self, conversation_about_git, temp_workspace):
        """Test LLM-based file selection."""
        config = FileSelectorConfig(strategy="llm")

        files = select_relevant_files(
            conversation_about_git,
            temp_workspace,
            max_files=3,
            use_selector=True,
            config=config,
        )

        # LLM should select relevant files
        assert len(files) <= 3
        assert len(files) > 0
        # Files should exist and be from workspace
        assert all(f.exists() for f in files)

    @_needs_llm
    @pytest.mark.slow
    @pytest.mark.xfail(
        reason="Needs more realistic file fixtures with substantial content"
    )
    def test_hybrid_file_selection(self, conversation_mixed_topics, temp_workspace):
        """Test hybrid file selection."""
        config = FileSelectorConfig(strategy="hybrid")

        files = select_relevant_files(
            conversation_mixed_topics,
            temp_workspace,
            max_files=5,
            use_selector=True,
            config=config,
        )

        # Hybrid should combine rule + LLM
        assert len(files) <= 5
        assert len(files) > 0
        # Files should exist and be from workspace
        assert all(f.exists() for f in files)
