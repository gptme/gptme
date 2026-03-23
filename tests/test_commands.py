"""Tests for the command registry, dispatcher, and command handlers.

Tests the core command infrastructure in gptme/commands/base.py:
- Command decorator and registration
- Dynamic command registration/unregistration
- Command dispatch (handle_cmd)
- execute_cmd for user messages
- Command descriptions and listing
- Auto-undo behavior
"""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.commands.base import (
    CommandContext,
    _command_completers,
    _command_registry,
    command,
    execute_cmd,
    get_command_completer,
    get_commands_with_descriptions,
    get_registered_commands,
    get_user_commands,
    handle_cmd,
    register_command,
    unregister_command,
)
from gptme.message import Message


@pytest.fixture()
def clean_registry():
    """Save and restore the command registry around each test.

    This allows tests to register/unregister commands without affecting
    other tests or the global state.
    """
    saved_registry = dict(_command_registry)
    saved_completers = dict(_command_completers)
    yield
    _command_registry.clear()
    _command_registry.update(saved_registry)
    _command_completers.clear()
    _command_completers.update(saved_completers)


@pytest.fixture()
def mock_manager():
    """Create a mock LogManager for command context."""
    manager = MagicMock()
    manager.log = MagicMock()
    manager.log.messages = []
    manager.logdir = Path("/tmp/test-conversation")
    manager.logfile = Path("/tmp/test-conversation/conversation.jsonl")
    manager.name = "test-conversation"
    manager.workspace = Path("/tmp/test-workspace")
    return manager


# ── CommandContext ──


class TestCommandContext:
    def test_basic_creation(self, mock_manager):
        ctx = CommandContext(
            args=["arg1", "arg2"], full_args="arg1 arg2", manager=mock_manager
        )
        assert ctx.args == ["arg1", "arg2"]
        assert ctx.full_args == "arg1 arg2"
        assert ctx.manager is mock_manager

    def test_empty_args(self, mock_manager):
        ctx = CommandContext(args=[], full_args="", manager=mock_manager)
        assert ctx.args == []
        assert ctx.full_args == ""


# ── Command Decorator ──


class TestCommandDecorator:
    def test_registers_command(self, clean_registry):
        @command("testcmd")
        def cmd_test(ctx: CommandContext) -> None:
            pass

        assert "testcmd" in _command_registry

    def test_registers_aliases(self, clean_registry):
        @command("testcmd", aliases=["tc", "t"])
        def cmd_test(ctx: CommandContext) -> None:
            pass

        assert "testcmd" in _command_registry
        assert "tc" in _command_registry
        assert "t" in _command_registry
        # All point to the same wrapper
        assert _command_registry["tc"] is _command_registry["testcmd"]

    def test_registers_completer(self, clean_registry):
        def my_completer(partial, prev):
            return [("opt1", "desc1")]

        @command("testcmd", completer=my_completer)
        def cmd_test(ctx: CommandContext) -> None:
            pass

        assert "testcmd" in _command_completers
        assert _command_completers["testcmd"] is my_completer

    def test_registers_completer_for_aliases(self, clean_registry):
        def my_completer(partial, prev):
            return []

        @command("testcmd", aliases=["tc"], completer=my_completer)
        def cmd_test(ctx: CommandContext) -> None:
            pass

        assert "tc" in _command_completers
        assert _command_completers["tc"] is my_completer

    def test_auto_undo_true(self, clean_registry, mock_manager):
        """auto_undo=True (default) calls manager.undo before handler."""
        called = []

        @command("testcmd")
        def cmd_test(ctx: CommandContext) -> None:
            called.append("handler")

        ctx = CommandContext(args=[], full_args="", manager=mock_manager)
        list(_command_registry["testcmd"](ctx))  # consume generator

        mock_manager.undo.assert_called_once_with(1, quiet=True)
        mock_manager.write.assert_called_once()
        assert "handler" in called

    def test_auto_undo_false(self, clean_registry, mock_manager):
        """auto_undo=False skips undo before handler."""

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            pass

        ctx = CommandContext(args=[], full_args="", manager=mock_manager)
        list(_command_registry["testcmd"](ctx))

        mock_manager.undo.assert_not_called()

    def test_generator_handler(self, clean_registry, mock_manager):
        """Handlers that yield Messages work correctly."""

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> Generator[Message, None, None]:
            yield Message("system", "test output")

        ctx = CommandContext(args=[], full_args="", manager=mock_manager)
        results = list(_command_registry["testcmd"](ctx))

        assert len(results) == 1
        assert results[0].role == "system"
        assert results[0].content == "test output"

    def test_returns_original_function(self, clean_registry):
        """The decorator returns the original function (not the wrapper)."""

        @command("testcmd")
        def cmd_test(ctx: CommandContext) -> None:
            pass

        # The returned function is the original, not the wrapper
        assert cmd_test.__name__ == "cmd_test"


# ── Dynamic Registration ──


class TestDynamicRegistration:
    def test_register_command(self, clean_registry):
        def handler(ctx):
            yield Message("system", "dynamic")

        register_command("dyncmd", handler)
        assert "dyncmd" in _command_registry
        assert _command_registry["dyncmd"] is handler

    def test_register_with_aliases(self, clean_registry):
        def handler(ctx):
            yield Message("system", "dynamic")

        register_command("dyncmd", handler, aliases=["dc"])
        assert "dc" in _command_registry
        assert _command_registry["dc"] is handler

    def test_register_with_completer(self, clean_registry):
        def handler(ctx):
            pass

        def completer(partial, prev):
            return []

        register_command("dyncmd", handler, completer=completer)
        assert _command_completers["dyncmd"] is completer

    def test_register_completer_for_aliases(self, clean_registry):
        def handler(ctx):
            pass

        def completer(partial, prev):
            return []

        register_command("dyncmd", handler, aliases=["dc"], completer=completer)
        assert _command_completers["dc"] is completer

    def test_unregister_command(self, clean_registry):
        def handler(ctx):
            pass

        register_command("dyncmd", handler)
        assert "dyncmd" in _command_registry

        unregister_command("dyncmd")
        assert "dyncmd" not in _command_registry

    def test_unregister_removes_completer(self, clean_registry):
        def handler(ctx):
            pass

        def completer(partial, prev):
            return []

        register_command("dyncmd", handler, completer=completer)
        unregister_command("dyncmd")
        assert "dyncmd" not in _command_completers

    def test_unregister_nonexistent_is_safe(self, clean_registry):
        """Unregistering a command that doesn't exist should not raise."""
        unregister_command("nonexistent_command_xyz")


# ── Query Functions ──


class TestQueryFunctions:
    def test_get_registered_commands(self):
        """Should return all registered command names."""
        commands = get_registered_commands()
        assert isinstance(commands, list)
        # Built-in commands should be registered
        assert "help" in commands
        assert "exit" in commands
        assert "log" in commands
        assert "undo" in commands

    def test_get_command_completer_exists(self):
        """Should return completer for commands that have one."""
        completer = get_command_completer("model")
        assert completer is not None
        assert callable(completer)

    def test_get_command_completer_none(self):
        """Should return None for commands without completer."""
        completer = get_command_completer("exit")
        assert completer is None

    def test_get_user_commands(self):
        """Should return /-prefixed command list."""
        commands = get_user_commands()
        assert all(cmd.startswith("/") for cmd in commands)
        assert "/help" in commands
        assert "/exit" in commands

    def test_get_commands_with_descriptions(self):
        """Should return sorted (name, description) tuples."""
        commands = get_commands_with_descriptions()
        assert isinstance(commands, list)
        assert len(commands) > 0
        # Check structure
        for name, desc in commands:
            assert isinstance(name, str)
            assert isinstance(desc, str)
            assert len(desc) > 0
        # Check sorted
        names = [n for n, _ in commands]
        assert names == sorted(names)

    def test_descriptions_use_action_descriptions(self):
        """Built-in commands should use action_descriptions."""
        commands = dict(get_commands_with_descriptions())
        assert "exit" in commands
        assert commands["exit"] == "Exit the program"

    def test_descriptions_dedup_aliases(self):
        """Aliases should not appear as separate entries."""
        commands = get_commands_with_descriptions()
        names = [n for n, _ in commands]
        # 'rm' is an alias for 'delete' — only one should appear
        assert not ("rm" in names and "delete" in names)


# ── handle_cmd ──


class TestHandleCmd:
    def test_dispatches_registered_command(self, clean_registry, mock_manager):
        called_with = []

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            called_with.append(ctx.full_args)

        list(handle_cmd("/testcmd hello world", mock_manager))
        assert called_with == ["hello world"]

    def test_parses_args(self, clean_registry, mock_manager):
        captured_args = []

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            captured_args.extend(ctx.args)

        list(handle_cmd("/testcmd arg1 arg2 arg3", mock_manager))
        assert captured_args == ["arg1", "arg2", "arg3"]

    def test_strips_leading_slash(self, clean_registry, mock_manager):
        called = []

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            called.append(True)

        list(handle_cmd("/testcmd", mock_manager))
        assert called == [True]

    def test_empty_args(self, clean_registry, mock_manager):
        captured: dict[str, object] = {}

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            captured["args"] = list(ctx.args)
            captured["full_args"] = ctx.full_args

        list(handle_cmd("/testcmd", mock_manager))
        assert captured["full_args"] == ""

    def test_yields_messages_from_handler(self, clean_registry, mock_manager):
        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> Generator[Message, None, None]:
            yield Message("system", "msg1")
            yield Message("system", "msg2")

        results = list(handle_cmd("/testcmd", mock_manager))
        assert len(results) == 2
        assert results[0].content == "msg1"
        assert results[1].content == "msg2"

    def test_unknown_command_falls_back_to_tool(self, clean_registry, mock_manager):
        """Unknown commands should try tool execution."""
        with patch("gptme.tools.ToolUse") as MockToolUse:
            mock_tooluse = MagicMock()
            mock_tooluse.is_runnable = False
            MockToolUse.return_value = mock_tooluse

            list(handle_cmd("/nonexistent_xyz", mock_manager))

            MockToolUse.assert_called_once_with("nonexistent_xyz", [], "")
            # When not runnable, undo and print error
            mock_manager.undo.assert_called_once_with(1, quiet=True)

    def test_unknown_command_runs_tool_if_runnable(self, clean_registry, mock_manager):
        """Unknown commands should execute as tool if runnable."""
        with patch("gptme.tools.ToolUse") as MockToolUse:
            mock_tooluse = MagicMock()
            mock_tooluse.is_runnable = True
            mock_tooluse.execute.return_value = iter([Message("system", "tool output")])
            MockToolUse.return_value = mock_tooluse

            results = list(handle_cmd("/sometool arg1", mock_manager))

            mock_tooluse.execute.assert_called_once()
            assert len(results) == 1
            assert results[0].content == "tool output"

    def test_newline_in_command_splits_args(self, clean_registry, mock_manager):
        """Arguments separated by newlines should be split correctly."""
        captured = {}

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> None:
            captured["args"] = list(ctx.args)

        list(handle_cmd("/testcmd arg1\narg2", mock_manager))
        assert "arg1" in captured["args"]
        assert "arg2" in captured["args"]


# ── execute_cmd ──


class TestExecuteCmd:
    def test_command_message_returns_true(self, mock_manager):
        """Messages starting with / (command) should return True."""
        msg = Message("user", "/help")
        result = execute_cmd(msg, mock_manager)
        assert result is True

    def test_non_command_returns_false(self, mock_manager):
        """Non-command messages should return False."""
        msg = Message("user", "Hello, how are you?")
        result = execute_cmd(msg, mock_manager)
        assert result is False

    def test_absolute_path_not_command(self, mock_manager):
        """Absolute paths (like /home/user) should not be treated as commands."""
        msg = Message("user", "/home/user/file.txt")
        result = execute_cmd(msg, mock_manager)
        assert result is False

    def test_command_appends_responses(self, clean_registry, mock_manager):
        """Command responses should be appended to the log."""

        @command("testcmd", auto_undo=False)
        def cmd_test(ctx: CommandContext) -> Generator[Message, None, None]:
            yield Message("system", "response")

        msg = Message("user", "/testcmd")
        execute_cmd(msg, mock_manager)
        mock_manager.append.assert_called()


# ── Built-in Commands Smoke Tests ──


class TestBuiltinCommands:
    """Smoke tests for built-in command handlers to verify they're registered and callable."""

    def test_builtin_commands_registered(self):
        """All expected built-in commands should be registered."""
        expected = [
            "help",
            "exit",
            "log",
            "undo",
            "edit",
            "rename",
            "fork",
            "delete",
            "clear",
            "model",
            "models",
            "context",
            "tokens",
            "tools",
            "replay",
            "export",
            "summarize",
            "plugin",
            "impersonate",
            "setup",
            "doctor",
            "restart",
            "compact",
        ]
        registered = get_registered_commands()
        for cmd in expected:
            assert cmd in registered, f"Expected command '{cmd}' not registered"

    def test_aliases_registered(self):
        """Command aliases should also be registered."""
        registered = get_registered_commands()
        assert "rm" in registered  # alias for delete
        assert "cls" in registered  # alias for clear
        assert "cost" in registered  # alias for tokens

    def test_log_command(self, mock_manager):
        """The /log command should call log.print."""
        list(handle_cmd("/log", mock_manager))
        mock_manager.log.print.assert_called()

    def test_log_command_hidden_flag(self, mock_manager):
        """The /log --hidden command should pass show_hidden=True."""
        list(handle_cmd("/log --hidden", mock_manager))
        mock_manager.log.print.assert_called_with(show_hidden=True)

    def test_undo_command_default(self, mock_manager):
        """The /undo command should undo 1 message (plus auto-undo of itself)."""
        list(handle_cmd("/undo", mock_manager))
        # auto_undo removes the /undo message first, then handler calls undo(1)
        assert mock_manager.undo.call_count == 2
        mock_manager.undo.assert_any_call(1, quiet=True)  # auto-undo
        mock_manager.undo.assert_any_call(1)  # handler

    def test_undo_command_with_count(self, mock_manager):
        """The /undo 3 command should undo 3 messages."""
        list(handle_cmd("/undo 3", mock_manager))
        mock_manager.undo.assert_any_call(3)

    def test_clear_command(self, mock_manager, capsys):
        """The /clear command should print ANSI clear sequence."""
        list(handle_cmd("/clear", mock_manager))
        captured = capsys.readouterr()
        assert "\033[2J" in captured.out

    def test_fork_command(self, mock_manager, capsys):
        """The /fork command should call manager.fork."""
        list(handle_cmd("/fork test-fork-name", mock_manager))
        mock_manager.fork.assert_called_once_with("test-fork-name")

    def test_export_command(self, mock_manager):
        """The /export command should call export_chat_to_html."""
        with patch("gptme.util.export.export_chat_to_html") as mock_export:
            list(handle_cmd("/export output.html", mock_manager))
            mock_export.assert_called_once()
            # First arg is conversation name, third arg is output path
            args = mock_export.call_args
            assert args[0][2] == Path("output.html")
