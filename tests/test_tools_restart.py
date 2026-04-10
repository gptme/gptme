"""Tests for the restart tool — process restart with argument filtering.

Tests cover:
- _do_restart: argument filtering (persisted flags, positional args, inline values)
- execute_restart: confirmation flow (accept/cancel)
- restart_hook: message detection, flag gating
- _FLAGS_WITH_VALUES: constant correctness
- tool spec: registration, hooks, disabled-by-default
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message
from gptme.tools.restart import (
    _FLAGS_WITH_VALUES,
    _do_restart,
    execute_restart,
    restart_hook,
    tool,
)

# ── Helpers ──────────────────────────────────────────────────────────────


class _RestartCalled(Exception):
    """Sentinel exception to simulate os.execv replacing the process."""


def _collect(gen):
    """Collect all messages from a generator."""
    return list(gen)


@pytest.fixture(autouse=True)
def _reset_restart_flag():
    """Reset the global _triggered_restart flag between tests."""
    import gptme.tools.restart as mod

    mod._triggered_restart = False
    yield
    mod._triggered_restart = False


# ── Constants ────────────────────────────────────────────────────────────


class TestFlagsWithValues:
    """Test _FLAGS_WITH_VALUES constant."""

    def test_contains_expected_flags(self):
        expected = {"--name", "-m", "--model", "-w", "--workspace", "--agent-path"}
        assert expected.issubset(_FLAGS_WITH_VALUES)

    def test_all_entries_start_with_dash(self):
        for flag in _FLAGS_WITH_VALUES:
            assert flag.startswith("-"), f"{flag} doesn't start with -"


# ── _do_restart argument filtering ───────────────────────────────────────


class TestDoRestartArgFiltering:
    """Test argument filtering in _do_restart."""

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_positional_args(self, mock_atexit, mock_execv):
        """Positional arguments (prompts) should be stripped."""
        with patch.object(sys, "argv", ["gptme", "hello world", "--verbose"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "hello world" not in args
        assert "--verbose" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_persisted_flags(self, mock_atexit, mock_execv):
        """Persisted flags (--model, --name, etc.) should be stripped."""
        with patch.object(sys, "argv", ["gptme", "--model", "gpt-4", "--verbose"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--model" not in args
        assert "gpt-4" not in args
        assert "--verbose" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_persisted_boolean_flags(self, mock_atexit, mock_execv):
        """Persisted boolean flags (--stream, --no-stream) should be stripped."""
        with patch.object(sys, "argv", ["gptme", "--stream", "--verbose"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--stream" not in args
        assert "--verbose" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_preserves_non_persisted_flags(self, mock_atexit, mock_execv):
        """Non-persisted flags should be kept."""
        with patch.object(sys, "argv", ["gptme", "--verbose", "--debug"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--verbose" in args
        assert "--debug" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_inline_persisted_flags(self, mock_atexit, mock_execv):
        """Persisted flags with inline values (--model=gpt-4) should be stripped."""
        with patch.object(sys, "argv", ["gptme", "--model=gpt-4", "--verbose"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--model=gpt-4" not in args
        assert "--verbose" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_preserves_non_persisted_inline_flags(self, mock_atexit, mock_execv):
        """Non-persisted inline flags should be kept."""
        with patch.object(
            sys, "argv", ["gptme", "--output-schema=foo.json", "--verbose"]
        ):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--output-schema=foo.json" in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_adds_conversation_name(self, mock_atexit, mock_execv):
        """Should add --name with conversation name when provided."""
        with patch.object(sys, "argv", ["gptme"]):
            _do_restart(conversation_name="my-chat")
        args = mock_execv.call_args[0][1]
        assert "--name" in args
        name_idx = args.index("--name")
        assert args[name_idx + 1] == "my-chat"

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_no_conversation_name(self, mock_atexit, mock_execv):
        """Without conversation name, --name should not appear."""
        with patch.object(sys, "argv", ["gptme"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--name" not in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_keeps_script_name(self, mock_atexit, mock_execv):
        """sys.argv[0] should always be the first argument."""
        with patch.object(sys, "argv", ["gptme", "prompt text"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert args[0] == "gptme"

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_execv_called_with_argv0(self, mock_atexit, mock_execv):
        """os.execv should be called with sys.argv[0] as executable."""
        with patch.object(sys, "argv", ["gptme"]):
            _do_restart()
        mock_execv.assert_called_once()
        assert mock_execv.call_args[0][0] == "gptme"

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_atexit_called_before_execv(self, mock_atexit, mock_execv):
        """atexit handlers should run before process replacement."""
        call_order = []
        mock_atexit.side_effect = lambda: call_order.append("atexit")
        mock_execv.side_effect = lambda *a: call_order.append("execv")
        with patch.object(sys, "argv", ["gptme"]):
            _do_restart()
        assert call_order == ["atexit", "execv"]

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_atexit_error_doesnt_prevent_restart(self, mock_atexit, mock_execv):
        """If atexit fails, restart should still proceed."""
        mock_atexit.side_effect = RuntimeError("cleanup failed")
        with patch.object(sys, "argv", ["gptme"]):
            _do_restart()
        mock_execv.assert_called_once()

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_resume_flag(self, mock_atexit, mock_execv):
        """-r/--resume should be filtered (persisted)."""
        with patch.object(sys, "argv", ["gptme", "-r", "--verbose"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "-r" not in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_tools_flag_with_value(self, mock_atexit, mock_execv):
        """--tools with a value should be filtered (persisted)."""
        with patch.object(
            sys, "argv", ["gptme", "--tools", "shell,python", "--verbose"]
        ):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "--tools" not in args
        assert "shell,python" not in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_filters_non_interactive_flag(self, mock_atexit, mock_execv):
        """-n/--non-interactive should be filtered."""
        with patch.object(sys, "argv", ["gptme", "-n"]):
            _do_restart()
        args = mock_execv.call_args[0][1]
        assert "-n" not in args

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_complex_argv(self, mock_atexit, mock_execv):
        """Test filtering with a realistic complex argv."""
        with patch.object(
            sys,
            "argv",
            [
                "gptme",
                "--model",
                "claude-3",
                "--verbose",
                "--stream",
                "--name",
                "old-chat",
                "fix the bug",
                "--debug",
            ],
        ):
            _do_restart(conversation_name="new-chat")
        args = mock_execv.call_args[0][1]
        # Script name kept
        assert args[0] == "gptme"
        # Persisted flags filtered
        assert "--model" not in args
        assert "claude-3" not in args
        assert "--stream" not in args
        assert "--name" not in args or args[args.index("--name") + 1] == "new-chat"
        assert "old-chat" not in args
        # Positional prompts filtered
        assert "fix the bug" not in args
        # Non-persisted flags kept
        assert "--verbose" in args
        assert "--debug" in args
        # New conversation name added
        assert "new-chat" in args


# ── execute_restart ──────────────────────────────────────────────────────


class TestExecuteRestart:
    """Test execute_restart confirmation flow."""

    def test_cancelled(self):
        with patch("gptme.tools.restart.confirm", return_value=False):
            msgs = _collect(execute_restart(None, None, None))
        assert len(msgs) == 1
        assert "cancelled" in msgs[0].content.lower()

    def test_confirmed(self):
        import gptme.tools.restart as mod

        with patch("gptme.tools.restart.confirm", return_value=True):
            msgs = _collect(execute_restart(None, None, None))
        assert len(msgs) == 1
        assert "Restarted" in msgs[0].content or "Restart" in msgs[0].content
        assert mod._triggered_restart is True

    def test_sets_triggered_flag(self):
        import gptme.tools.restart as mod

        assert mod._triggered_restart is False
        with patch("gptme.tools.restart.confirm", return_value=True):
            _collect(execute_restart(None, None, None))
        assert mod._triggered_restart is True


# ── restart_hook ─────────────────────────────────────────────────────────


class TestRestartHook:
    """Test restart_hook detection logic."""

    def test_empty_messages(self):
        msgs = _collect(restart_hook([]))
        assert msgs == []

    def test_no_assistant_message(self):
        msgs = _collect(restart_hook([Message("user", "hello")]))
        assert msgs == []

    def test_no_restart_tool_call(self):
        msgs = _collect(
            restart_hook([Message("assistant", "I'll help you with that.")])
        )
        assert msgs == []

    def test_restart_not_triggered(self):
        """Even with restart tool call, hook should not fire without flag."""
        import gptme.tools.restart as mod

        mod._triggered_restart = False
        # Simulate a message with restart tool use
        content = "I'll restart now.\n```restart\n\n```"
        msgs = _collect(restart_hook([Message("assistant", content)]))
        assert msgs == []

    def test_restart_triggered_calls_do_restart(self):
        """When flag is set and restart tool found, should call _do_restart."""
        import gptme.tools.restart as mod
        from gptme.tools.base import ToolUse

        mod._triggered_restart = True
        content = "I'll restart now.\n```restart\n\n```"
        fake_use = ToolUse("restart", [], "", start=0, _format="markdown")
        # _do_restart normally calls os.execv which replaces the process;
        # simulate that with an exception so code after it doesn't run.
        with (
            patch(
                "gptme.tools.restart._do_restart", side_effect=_RestartCalled
            ) as mock_restart,
            patch.object(ToolUse, "iter_from_content", return_value=iter([fake_use])),
            patch("gptme.logmanager.LogManager") as mock_lm_cls,
            pytest.raises(_RestartCalled),
        ):
            mock_lm = MagicMock()
            mock_lm.logfile.parent.name = "test-chat"
            mock_lm_cls.get_current_log.return_value = mock_lm
            _collect(restart_hook([Message("assistant", content)]))
        mock_restart.assert_called_once_with("test-chat")

    def test_restart_triggered_no_logmanager(self):
        """Should still restart even if LogManager fails."""
        import gptme.tools.restart as mod
        from gptme.tools.base import ToolUse

        mod._triggered_restart = True
        content = "I'll restart now.\n```restart\n\n```"
        fake_use = ToolUse("restart", [], "", start=0, _format="markdown")
        with (
            patch(
                "gptme.tools.restart._do_restart", side_effect=_RestartCalled
            ) as mock_restart,
            patch.object(ToolUse, "iter_from_content", return_value=iter([fake_use])),
            patch(
                "gptme.logmanager.LogManager.get_current_log",
                side_effect=Exception("test"),
            ),
            pytest.raises(_RestartCalled),
        ):
            _collect(restart_hook([Message("assistant", content)]))
        mock_restart.assert_called_once_with(None)


# ── Tool spec ────────────────────────────────────────────────────────────


class TestToolSpec:
    """Test tool specification."""

    def test_tool_name(self):
        assert tool.name == "restart"

    def test_disabled_by_default(self):
        assert tool.disabled_by_default is True

    def test_has_execute(self):
        assert tool.execute is not None

    def test_block_types(self):
        assert "restart" in tool.block_types

    def test_has_hooks(self):
        assert "restart" in tool.hooks
        hook_tuple = tool.hooks["restart"]
        assert hook_tuple[0] == "generation.pre"
        assert hook_tuple[2] == 1000  # high priority

    def test_has_description(self):
        assert tool.desc and len(tool.desc) > 0

    def test_has_instructions(self):
        assert tool.instructions and len(tool.instructions) > 0

    def test_has_examples(self):
        examples = tool.get_examples()
        assert examples and "restart" in examples
