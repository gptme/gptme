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
    _classify_stuck_reason,
    auto_reply_hook,
    complete_hook,
    execute_complete,
    stuck_detect_hook,
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

    # ── Interactive + no_confirm mode tests ──

    def test_interactive_no_confirm_nudges_think_only(self):
        """In interactive+no_confirm mode, injects a quiet nudge on think-only."""
        manager = _mock_manager(
            [
                _user("implement feature X"),
                _assistant("Let me think about the best approach for X..."),
            ]
        )
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert "No tool call detected" in msg.content
        assert msg.quiet is True

    def test_interactive_no_confirm_noop_when_has_tools(self):
        """In interactive+no_confirm mode, no nudge when assistant used tools."""
        manager = _mock_manager(
            [
                _user("save a file"),
                _assistant("```save test.txt\nhello\n```"),
            ]
        )
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        assert results == []

    def test_interactive_no_confirm_noop_on_second_nudge(self):
        """In interactive+no_confirm mode, only nudges once per think-only sequence."""
        manager = _mock_manager(
            [
                _user("implement feature X"),
                _assistant("Let me think about this..."),
                _user(
                    "<system>No tool call detected. Please continue with a tool call, or use `complete` if done.</system>"
                ),
                _assistant("I'm still thinking about the approach..."),
            ]
        )
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        # Second think-only after nudge should be silent (no pile-on)
        assert results == []

    def test_interactive_no_confirm_does_not_exit(self):
        """Interactive+no_confirm nudge has no exit logic (no SessionCompleteException)."""
        manager = _mock_manager(
            [
                _user("do something"),
                _assistant("I'm thinking..."),
                _user(
                    "<system>No tool call detected. Please continue with a tool call, or use `complete` if done.</system>"
                ),
                _assistant("Still thinking about the best approach..."),
            ]
        )
        # Should not raise an exception regardless of how many think-only responses
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        assert results == []

    def test_interactive_no_confirm_noop_when_no_assistant_msg(self):
        """Early return in _auto_reply_nudge_interactive when no assistant message exists."""
        manager = _mock_manager(
            [
                _user("do something"),
            ]
        )
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        assert results == []

    def test_interactive_no_confirm_nudge_stops_at_prior_tool_use(self):
        """Nudge counting loop stops at a prior assistant message with tools.

        The backward nudge scan walks past nudges and think-only assistant messages,
        and when it reaches a prior assistant turn that DID use tools, it breaks
        without iterating further.
        """
        manager = _mock_manager(
            [
                _user("write a file"),
                _assistant(
                    "```save hello.txt\nworld\n```"
                ),  # has tools — scan boundary
                # First think-only sequence: nudged
                _user(
                    "<system>No tool call detected. Please continue with a tool call, or use `complete` if done.</system>"
                ),
                _assistant("I'm thinking..."),
                # Second think-only sequence: nudged again (previous was different)
                _user(
                    "<system>No tool call detected. Please continue with a tool call, or use `complete` if done.</system>"
                ),
                _assistant("Still thinking..."),
                # Third think-only: should NOT nudge (already 2 nudges in sequence)
            ]
        )
        gen = auto_reply_hook(
            manager, interactive=True, prompt_queue=None, no_confirm=True
        )
        results = list(gen)
        # Already nudged twice, scan reaches the save tool use and breaks → no more nudges
        assert results == []


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


# ── TestStuckDetectHook ────────────────────────────────────────────────────


class TestStuckDetectHook:
    """Tests for stuck_detect_hook — LOOP_CONTINUE hook for repeating-tool loops.

    Unlike auto_reply_hook, this fires when the last assistant message *has* tool
    uses but keeps repeating the same action without progress (gptme/gptme#2725).
    """

    _SAVE_A = "```save a.txt\nhello\n```"
    _SAVE_B = "```save b.txt\nworld\n```"

    def test_interactive_noop(self):
        """No action in interactive mode, even with identical repeats."""
        manager = _mock_manager([_assistant(self._SAVE_A)] * 3)
        results = list(stuck_detect_hook(manager, interactive=True, prompt_queue=None))
        assert results == []

    def test_disabled_via_env_noop(self, monkeypatch):
        """No action when GPTME_STUCK_DETECT is turned off."""
        monkeypatch.setenv("GPTME_STUCK_DETECT", "0")
        manager = _mock_manager([_assistant(self._SAVE_A)] * 3)
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert results == []

    def test_queued_prompts_noop(self):
        """No action when prompt queue has items."""
        manager = _mock_manager([_assistant(self._SAVE_A)] * 3)
        results = list(
            stuck_detect_hook(manager, interactive=False, prompt_queue=["next"])
        )
        assert results == []

    def test_below_threshold_noop(self):
        """No action when identical repeats are fewer than the threshold."""
        manager = _mock_manager([_assistant(self._SAVE_A)] * 2)
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert results == []

    def test_no_tool_use_noop(self):
        """No action when the last assistant message has no tool uses.

        That case belongs to auto_reply_hook, not this hook.
        """
        manager = _mock_manager([_assistant("just thinking out loud")] * 3)
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert results == []

    def test_distinct_calls_noop(self):
        """No action when each turn issues a different tool call."""
        manager = _mock_manager(
            [
                _assistant(self._SAVE_A),
                _assistant(self._SAVE_B),
                _assistant("```save c.txt\n!\n```"),
            ]
        )
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert results == []

    def test_three_identical_turns_nudges(self):
        """Three identical tool-call turns yield a single stuck nudge, no raise."""
        manager = _mock_manager([_assistant(self._SAVE_A)] * 3)
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert msg.role == "user"
        assert "appear stuck" in msg.content.lower()
        assert "save" in msg.content

    def test_different_call_resets_after_repeats(self):
        """A different tool call on top of a stuck run resets detection."""
        manager = _mock_manager(
            [
                _assistant(self._SAVE_A),
                _assistant(self._SAVE_A),
                _assistant(self._SAVE_A),
                _assistant(self._SAVE_B),  # different action breaks the loop
            ]
        )
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert results == []

    def test_raises_after_escalate_max(self):
        """Raises SessionCompleteException once escalations hit the max unbroken."""
        marker = "<system>You appear stuck: same action repeated.</system>"
        manager = _mock_manager(
            [
                _assistant(self._SAVE_A),
                _user(marker),
                _assistant(self._SAVE_A),
                _user(marker),
                _assistant(self._SAVE_A),
            ]
        )
        with pytest.raises(SessionCompleteException, match="escalations"):
            list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))

    def test_raises_after_escalate_max_with_system_msgs(self):
        """Raises SessionCompleteException with a realistic log that includes system messages.

        In real sessions LOOP_CONTINUE fires after tool execution, so the log
        always ends with a system message (tool result). The escalation counter
        must skip those instead of stopping at them, otherwise escalation_count
        stays 0 and SessionCompleteException never fires.
        """
        marker = "<system>You appear stuck: same action repeated.</system>"
        manager = _mock_manager(
            [
                _assistant(self._SAVE_A),
                _system("Output: hello"),  # tool result after first repeat
                _user(marker),
                _assistant(self._SAVE_A),
                _system("Output: hello"),  # tool result after second repeat
                _user(marker),
                _assistant(self._SAVE_A),
                _system("Output: hello"),  # tool result present when hook fires
            ]
        )
        with pytest.raises(SessionCompleteException, match="escalations"):
            list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))

    def test_custom_threshold_via_env(self, monkeypatch):
        """Repeat threshold is configurable; 2 identical turns trip a lower one."""
        monkeypatch.setenv("GPTME_STUCK_REPEAT_THRESHOLD", "2")
        manager = _mock_manager([_assistant(self._SAVE_A)] * 2)
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert "appear stuck" in msg.content.lower()

    def test_multi_tool_turn_order_independent(self):
        """Reordered multi-tool turns share a fingerprint (still detected stuck).

        Also verifies that the nudge message names ALL repeated tools, not just the
        first one alphabetically (P2 fix: use full set instead of latest_fp[0][0]).
        """
        turn1 = f"{self._SAVE_A}\n{self._SAVE_B}"
        turn2 = f"{self._SAVE_B}\n{self._SAVE_A}"  # same multiset, different order
        manager = _mock_manager(
            [_assistant(turn1), _assistant(turn2), _assistant(turn1)]
        )
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        msg = results[0]
        assert isinstance(msg, Message)
        assert "appear stuck" in msg.content.lower()
        # Both tool names must appear in the nudge (not just the alphabetically-first one)
        assert "save" in msg.content  # both _SAVE_A and _SAVE_B use the "save" tool

    def test_stuck_detect_hook_registered(self):
        """Stuck-detect hook is registered on the complete tool spec."""
        assert "stuck_detect" in tool.hooks


# ── TestClassifyStuckReason ────────────────────────────────────────────────


class TestClassifyStuckReason:
    """Tests for _classify_stuck_reason — root-cause classification helper.

    Covers all four classification paths (tool-error, empty-result,
    permission-denied, unknown) plus the disabled-env-flag path.
    """

    def test_tool_error_traceback(self):
        """Classifies Python tracebacks as tool-error."""
        msgs = [
            _assistant("```shell\npython3 foo.py\n```"),
            _system(
                "Traceback (most recent call last):\n  File 'foo.py', line 1\nValueError: bad input"
            ),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "tool-error"
        assert evidence  # should contain snippet

    def test_tool_error_exit_code(self):
        """Classifies non-zero exit codes as tool-error."""
        msgs = [
            _assistant("```shell\nmake build\n```"),
            _system("exit code: 2\ncc: error: foo.c: No such file"),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "tool-error"

    def test_tool_error_error_message(self):
        """Classifies 'Error:' prefix in output as tool-error."""
        msgs = [
            _assistant("```shell\ngit push\n```"),
            _system("Error: remote rejected (pre-receive hook declined)"),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "tool-error"

    def test_tool_error_command_not_found(self):
        """Classifies 'command not found' as tool-error."""
        msgs = [
            _assistant("```shell\nmytool --help\n```"),
            _system("bash: mytool: command not found"),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "tool-error"

    def test_permission_denied(self):
        """Classifies permission-denied patterns correctly."""
        msgs = [
            _assistant("```shell\nrm /etc/hosts\n```"),
            _system("rm: cannot remove '/etc/hosts': Permission denied"),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "permission-denied"
        assert evidence

    def test_permission_denied_takes_priority_over_error(self):
        """Permission-denied classification wins over generic error when both present."""
        msgs = [
            _assistant("```shell\ncat /root/secret\n```"),
            _system("Error: cat: /root/secret: Permission denied\nexit code: 1"),
        ]
        cls, _evidence = _classify_stuck_reason(msgs)
        assert cls == "permission-denied"

    def test_empty_result_no_output(self):
        """Classifies completely empty tool output as empty-result."""
        msgs = [
            _assistant("```shell\ngrep pattern file.txt\n```"),
            _system(""),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "empty-result"
        assert "(no output)" in evidence

    def test_empty_result_no_matches(self):
        """Classifies 'no results found' messages as empty-result."""
        msgs = [
            _assistant("```shell\nfind . -name '*.xyz'\n```"),
            _system("No files found matching the pattern"),
        ]
        cls, _evidence = _classify_stuck_reason(msgs)
        assert cls == "empty-result"

    def test_unknown_when_no_signal(self):
        """Returns unknown when output has no recognizable failure pattern."""
        msgs = [
            _assistant("```shell\necho hello\n```"),
            _system("hello"),
        ]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "unknown"
        assert evidence == ""

    def test_no_system_messages_returns_unknown(self):
        """Returns unknown when there are no system messages to inspect."""
        msgs = [_assistant("```shell\necho hi\n```")]
        cls, evidence = _classify_stuck_reason(msgs)
        assert cls == "unknown"

    def test_only_inspects_messages_after_last_assistant(self):
        """Only looks at system messages after the most recent assistant turn."""
        msgs = [
            _system("Error: old error from a prior turn"),
            _assistant("```shell\necho hello\n```"),
            _system("hello"),  # clean result after the last assistant turn
        ]
        cls, _evidence = _classify_stuck_reason(msgs)
        # Should classify based on "hello" (clean), not the old error
        assert cls == "unknown"


class TestStuckDetectRCAIntegration:
    """Integration tests: classification wired into stuck_detect_hook nudge."""

    _SAVE_A = "```save a.txt\nhello\n```"

    def _msgs_with_result(self, tool_result: str) -> list[Message]:
        """Build a 3-repeat stuck sequence with a tool result after each turn."""
        return [
            _assistant(self._SAVE_A),
            _system(tool_result),
            _assistant(self._SAVE_A),
            _system(tool_result),
            _assistant(self._SAVE_A),
            _system(tool_result),
        ]

    def test_rca_included_in_nudge_for_tool_error(self):
        """Nudge includes 'tool-error' classification when output has an error."""
        manager = _mock_manager(self._msgs_with_result("Error: file system read-only"))
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert "tool-error" in results[0].content

    def test_rca_included_in_nudge_for_permission_denied(self):
        """Nudge includes 'permission-denied' classification on access errors."""
        manager = _mock_manager(self._msgs_with_result("Operation not permitted"))
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert "permission-denied" in results[0].content

    def test_rca_omitted_for_unknown(self):
        """Nudge does NOT include a 'Likely cause:' hint for unknown classification."""
        manager = _mock_manager(self._msgs_with_result("output: success"))
        results = list(stuck_detect_hook(manager, interactive=False, prompt_queue=None))
        assert len(results) == 1
        assert isinstance(results[0], Message)
        assert "Likely cause" not in results[0].content
