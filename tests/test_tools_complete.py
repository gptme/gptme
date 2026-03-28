"""Tests for the complete tool — autonomous session completion signaling.

Tests cover:
- execute_complete: basic execution and return message
- SessionCompleteException: exception type
- complete_hook: GENERATION_PRE hook for detecting complete tool calls
  - empty messages, no assistant messages, no complete call
  - complete call detection and SessionCompleteException
  - multi-turn: only checks current turn (after last user message)
  - multiple tool uses in one message
- auto_reply_hook: LOOP_CONTINUE hook for autonomous auto-reply
  - interactive mode (no-op)
  - queued prompts (no-op)
  - no assistant messages (no-op)
  - assistant with tools (no-op)
  - first auto-reply without incomplete todos
  - first auto-reply with incomplete todos
  - exit after 2 consecutive auto-replies without tools
- tool spec: registration, hooks, block_types, disabled_by_default
"""

from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.complete import (
    SessionCompleteException,
    auto_reply_hook,
    complete_hook,
    execute_complete,
    tool,
)


@pytest.fixture(autouse=True)
def _init_complete_tool():
    """Initialize tools needed by these tests.

    The complete tool is disabled_by_default, so the tool registry won't
    recognize ```complete``` blocks unless we explicitly load it. This file also
    uses ```save ...``` blocks to exercise "assistant used a tool" paths, so we
    load that tool explicitly too instead of depending on broader test init.

    Must run per-test because conftest's clear_tools_before wipes the registry.
    """
    from gptme.tools import init_tools

    init_tools(allowlist=["complete", "save"])


# ── Helpers ───────────────────────────────────────────────────────────────


def _msg(role: Literal["system", "user", "assistant"], content: str) -> Message:
    """Create a Message with given role and content."""
    return Message(role, content)


def _assistant(content: str) -> Message:
    return _msg("assistant", content)


def _user(content: str) -> Message:
    return _msg("user", content)


def _system(content: str) -> Message:
    return _msg("system", content)


def _mock_manager(messages: list[Message]) -> MagicMock:
    """Create a mock LogManager with given messages."""
    manager = MagicMock()
    manager.log.messages = messages
    manager.workspace = MagicMock()
    return manager


# ── TestExecuteComplete ─────────────────────���─────────────────────────────


class TestExecuteComplete:
    """Tests for execute_complete — the basic tool execution."""

    def test_returns_system_message(self):
        """Returns a system message indicating completion."""
        result = execute_complete(None, None, None)
        assert result.role == "system"
        assert (
            "complete" in result.content.lower() or "finished" in result.content.lower()
        )

    def test_with_code_arg(self):
        """Works when code argument is provided."""
        result = execute_complete("some code", None, None)
        assert result.role == "system"

    def test_with_args(self):
        """Works when args are provided."""
        result = execute_complete(None, ["arg1"], None)
        assert result.role == "system"

    def test_with_kwargs(self):
        """Works when kwargs are provided."""
        result = execute_complete(None, None, {"key": "value"})
        assert result.role == "system"

    def test_message_not_quiet(self):
        """Message is not marked as quiet (should be visible)."""
        result = execute_complete(None, None, None)
        assert result.quiet is False


# ── TestSessionCompleteException ─────────────────���────────────────────────


class TestSessionCompleteException:
    """Tests for the SessionCompleteException type."""

    def test_is_exception(self):
        """SessionCompleteException is a proper Exception subclass."""
        exc = SessionCompleteException("test")
        assert isinstance(exc, Exception)

    def test_message(self):
        """Exception preserves message."""
        exc = SessionCompleteException("session done")
        assert str(exc) == "session done"

    def test_can_be_caught(self):
        """Can be caught specifically."""
        with pytest.raises(SessionCompleteException):
            raise SessionCompleteException("done")


# ── TestCompleteHook ─────────────────────────────────────────────���────────


class TestCompleteHook:
    """Tests for complete_hook — GENERATION_PRE hook that detects complete calls."""

    def test_empty_messages(self):
        """No exception raised for empty message list."""
        gen = complete_hook([])
        results = list(gen)
        assert results == []

    def test_system_only_messages(self):
        """No exception when only system messages are present (no assistant turn)."""
        messages = [_system("Session started."), _system("Tools loaded.")]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_only_user_messages(self):
        """No exception when only user messages present."""
        messages = [_user("hello"), _user("how are you")]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_only_system_messages(self):
        """No exception when only system messages present."""
        messages = [_system("initialized"), _system("ready")]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_assistant_without_complete(self):
        """No exception when assistant message has no complete tool call."""
        messages = [
            _user("do something"),
            _assistant("I'll help you with that. Here's the plan."),
        ]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_assistant_with_other_tool(self):
        """No exception when assistant uses a different tool."""
        messages = [
            _user("save a file"),
            _assistant("```save test.txt\nhello\n```"),
        ]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_assistant_with_complete_call(self):
        """Raises SessionCompleteException when complete tool is called."""
        messages = [
            _user("finish up"),
            _assistant("All done.\n```complete\n```"),
        ]
        with pytest.raises(SessionCompleteException):
            gen = complete_hook(messages)
            list(gen)

    def test_complete_in_earlier_turn_ignored(self):
        """Complete in a previous turn (before last user message) is ignored."""
        messages = [
            _user("first task"),
            _assistant("Done.\n```complete\n```"),
            _system("Task complete. Autonomous session finished."),
            _user("actually do one more thing"),  # New user message = new turn
        ]
        # No assistant message after the last user message, so no exception
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_complete_only_in_current_turn(self):
        """Only checks messages after the last user message."""
        messages = [
            _user("first task"),
            _assistant("Done.\n```complete\n```"),  # Old turn
            _user("second task"),
            _assistant("Working on second task.\n```complete\n```"),  # Current turn
        ]
        with pytest.raises(SessionCompleteException):
            gen = complete_hook(messages)
            list(gen)

    def test_multiple_tools_with_complete(self):
        """Detects complete even when mixed with other tool calls."""
        messages = [
            _user("wrap up"),
            _assistant(
                "Saving final file.\n```save output.txt\nresult\n```\n\nAll done.\n```complete\n```"
            ),
        ]
        with pytest.raises(SessionCompleteException):
            gen = complete_hook(messages)
            list(gen)

    def test_no_assistant_in_current_turn(self):
        """No exception when user just sent a message (no response yet)."""
        messages = [
            _assistant("I did something earlier"),
            _user("now do this"),
        ]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []

    def test_assistant_with_complete_like_text(self):
        """No false positive on text that mentions 'complete' but isn't a tool call."""
        messages = [
            _user("is the task complete?"),
            _assistant("Yes, the task is complete. Everything looks good."),
        ]
        gen = complete_hook(messages)
        results = list(gen)
        assert results == []


# ── TestAutoReplyHook ─────────────────────────────────────────────────────


class TestAutoReplyHook:
    """Tests for auto_reply_hook — LOOP_CONTINUE hook for autonomous auto-reply."""

    def test_interactive_mode_noop(self):
        """No action in interactive mode."""
        manager = _mock_manager([_assistant("hello")])
        gen = auto_reply_hook(manager, interactive=True, prompt_queue=None)
        results = list(gen)
        assert results == []

    def test_queued_prompts_noop(self):
        """No action when prompt queue has items."""
        manager = _mock_manager([_assistant("hello")])
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=["next prompt"])
        results = list(gen)
        assert results == []

    def test_no_assistant_messages_noop(self):
        """No action when there are no assistant messages."""
        manager = _mock_manager([_user("hello")])
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
        results = list(gen)
        assert results == []

    def test_assistant_with_tools_noop(self):
        """No action when last assistant message has tool calls."""
        manager = _mock_manager(
            [
                _user("save a file"),
                _assistant("```save test.txt\nhello\n```"),
            ]
        )
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
        results = list(gen)
        assert results == []

    @patch("gptme.tools.complete.has_incomplete_todos", return_value=False)
    def test_first_auto_reply_no_todos(self, mock_todos):
        """First auto-reply asks about completion when no incomplete todos."""
        manager = _mock_manager(
            [
                _user("do something"),
                _assistant("I think we're done here."),
            ]
        )
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
        results = list(gen)
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert "complete" in msg.content.lower()
        assert "tool" in msg.content.lower()

    @patch(
        "gptme.tools.complete.get_incomplete_todos_summary",
        return_value="- [ ] Fix bug\n- [ ] Write test",
    )
    @patch("gptme.tools.complete.has_incomplete_todos", return_value=True)
    def test_first_auto_reply_with_todos(self, mock_has, mock_summary):
        """First auto-reply reminds about incomplete todos when present."""
        manager = _mock_manager(
            [
                _user("do something"),
                _assistant("I think we're done."),
            ]
        )
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
        results = list(gen)
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert "incomplete todos" in msg.content.lower() or "Fix bug" in msg.content

    def test_exit_after_two_auto_replies(self):
        """Raises SessionCompleteException after 2 consecutive auto-replies without tools."""
        manager = _mock_manager(
            [
                _user("do something"),
                _assistant("I'm thinking about it."),
                _user(
                    "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>"
                ),
                _assistant("Yes, I believe we're done."),
                _user(
                    "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>"
                ),
                _assistant("Nothing more to do."),
            ]
        )
        with pytest.raises(SessionCompleteException, match="2 auto-reply"):
            gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
            list(gen)

    @patch("gptme.tools.complete.has_incomplete_todos", return_value=False)
    def test_counter_resets_after_tool_use(self, mock_todos):
        """Auto-reply counter resets when assistant uses a tool."""
        manager = _mock_manager(
            [
                _user("do something"),
                _assistant("I'm thinking."),
                _user(
                    "<system>No tool call detected in last message. Did you mean to finish? If so, make sure you are completely done and then use the `complete` tool to end the session.</system>"
                ),
                _assistant("```save test.txt\nhello\n```"),  # Tool use resets counter
                _assistant("Done with the file, anything else?"),
            ]
        )
        gen = auto_reply_hook(manager, interactive=False, prompt_queue=None)
        results = list(gen)
        # Should get a normal auto-reply (counter reset), not SessionCompleteException
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "user"


# ── TestToolSpec ──────────────────────���───────────────────────────────────


class TestToolSpec:
    """Tests for the complete tool spec configuration."""

    def test_tool_name(self):
        assert tool.name == "complete"

    def test_disabled_by_default(self):
        """Complete tool is disabled by default (only for autonomous)."""
        assert tool.disabled_by_default is True

    def test_block_types(self):
        """Tool recognizes 'complete' block type."""
        assert "complete" in tool.block_types

    def test_has_execute(self):
        """Tool has an execute function."""
        assert tool.execute is not None

    def test_has_hooks(self):
        """Tool has hooks registered."""
        assert tool.hooks is not None
        assert len(tool.hooks) >= 2

    def test_complete_hook_registered(self):
        """Complete detection hook is registered."""
        assert "complete" in tool.hooks

    def test_auto_reply_hook_registered(self):
        """Auto-reply hook is registered."""
        assert "auto_reply" in tool.hooks

    def test_has_instructions(self):
        """Tool has instructions for the LLM."""
        assert tool.instructions
        instructions = tool.instructions
        assert isinstance(instructions, str)
        assert "complete" in instructions.lower()

    def test_has_examples(self):
        """Tool has usage examples."""
        assert tool.examples
        examples = tool.examples
        assert isinstance(examples, str)
        assert "complete" in examples.lower()
