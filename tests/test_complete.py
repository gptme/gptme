"""Tests for complete tool and auto-reply hook."""

import pytest
from unittest.mock import patch

from gptme.message import Message
from gptme.tools.base import ToolUse
from gptme.tools.complete import SessionCompleteException, auto_reply_hook


class TestAutoReplyHook:
    """Tests for auto_reply_hook behavior."""

    def test_no_auto_replies(self):
        """Should not exit when no auto-replies have been sent."""
        log = [
            Message("assistant", "Let me work on this\n```shell\nls\n```"),
            Message("user", "Please continue"),
            Message("assistant", "Some response without tools"),
        ]

        # Should yield auto-reply message, not raise
        result = list(auto_reply_hook(log, workspace=None, interactive=False))
        assert len(result) == 1
        assert "use the `complete` tool" in result[0].content

    def test_one_auto_reply(self):
        """Should not exit after just one auto-reply."""
        log = [
            Message("assistant", "Working\n```shell\nls\n```"),
            Message(
                "user",
                "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            ),
            Message("assistant", "Still working without tools"),
        ]

        # Should yield second auto-reply, not raise
        result = list(auto_reply_hook(log, workspace=None, interactive=False))
        assert len(result) == 1
        assert "use the `complete` tool" in result[0].content

    def test_two_auto_replies_exits(self):
        """Should exit after two auto-replies without tools."""
        log = [
            Message("assistant", "Working\n```shell\nls\n```"),
            Message(
                "user",
                "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            ),
            Message("assistant", "First response without tools"),
            Message(
                "user",
                "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            ),
            Message("assistant", "Second response without tools"),
        ]

        # Should raise SessionCompleteException
        with pytest.raises(SessionCompleteException) as exc_info:
            list(auto_reply_hook(log, workspace=None, interactive=False))

        assert "2 auto-reply confirmations" in str(exc_info.value)

    def test_auto_reply_with_tools_resets_count(self):
        """Should reset count when tools are used after auto-reply."""
        log = [
            Message("assistant", "Initial work\n```shell\nls\n```"),
            Message(
                "user",
                "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            ),
            Message(
                "assistant", "Doing more work\n```shell\npwd\n```"
            ),  # Tools used - resets count
            Message(
                "user",
                "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>",
            ),
            Message("assistant", "Another response without tools"),
        ]

        # Mock tool detection to return tools for messages with shell blocks
        def mock_iter_from_content(content):
            if "```shell" in content and "```" in content:
                yield ToolUse(tool="shell", args=[], content="ls", kwargs={})

        with patch.object(
            ToolUse, "iter_from_content", side_effect=mock_iter_from_content
        ):
            # Should yield auto-reply (only 1 in consecutive sequence), not raise
            result = list(auto_reply_hook(log, workspace=None, interactive=False))
            assert len(result) == 1
            assert "use the `complete` tool" in result[0].content

    def test_interactive_mode_skips_hook(self):
        """Should not run in interactive mode."""
        log = [
            Message("assistant", "Response without tools"),
        ]

        # Should return None in interactive mode
        result = list(auto_reply_hook(log, workspace=None, interactive=True))
        assert len(result) == 0

    def test_queued_prompts_skips_hook(self):
        """Should not run when prompts are queued."""
        log = [
            Message("assistant", "Response without tools"),
        ]

        # Should return None when prompts are queued
        result = list(
            auto_reply_hook(
                log, workspace=None, interactive=False, prompt_queue=["some prompt"]
            )
        )
        assert len(result) == 0

    def test_assistant_with_tools_skips_hook(self):
        """Should not run when assistant used tools."""
        log = [
            Message("assistant", "Working on it\n```shell\nls -la\n```"),
        ]

        # Mock tool detection to return tools
        def mock_iter_from_content(content):
            if "```shell" in content:
                yield ToolUse(tool="shell", args=[], content="ls -la", kwargs={})

        with patch.object(
            ToolUse, "iter_from_content", side_effect=mock_iter_from_content
        ):
            # Should return None when tools were used
            result = list(auto_reply_hook(log, workspace=None, interactive=False))
            assert len(result) == 0
