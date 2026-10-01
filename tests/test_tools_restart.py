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

from gptme.hooks import HookType
from gptme.message import Message
from gptme.tools.restart import (
    _FLAGS_WITH_VALUES,
    RestartError,
    _do_restart,
    _http_status,
    build_restart_argv,
    execute_restart,
    get_server_url,
    open_web,
    parse_restart_target,
    prepare_web_switch,
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


# ── Interface switching (/restart cli|tui|web) ───────────────────────────


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """A venv-like bin dir holding the interpreter and both console scripts."""
    import gptme.tools.restart as mod

    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("python", "gptme", "gptme-tui"):
        script = bindir / name
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(bindir / "python"))
    # pretend textual is installed regardless of the test environment
    monkeypatch.setattr(mod, "check_interface_available", lambda target: None)
    return bindir


class TestParseRestartTarget:
    def test_no_args_is_same_interface(self):
        assert parse_restart_target([]) is None

    @pytest.mark.parametrize("target", ["cli", "tui", "web"])
    def test_known_targets(self, target):
        assert parse_restart_target([target]) == target

    @pytest.mark.parametrize("args", [["gui"], ["tui", "extra"]])
    def test_invalid(self, args):
        with pytest.raises(RestartError, match="Usage: /restart"):
            parse_restart_target(args)

    def test_completer(self):
        from gptme.commands import get_command_completer

        completer = get_command_completer("restart")
        assert completer is not None
        assert [c for c, _ in completer("", [])] == ["cli", "tui", "web"]
        assert [c for c, _ in completer("t", [])] == ["tui"]
        assert completer("", ["tui"]) == []


class TestBuildRestartArgv:
    def test_cli_to_tui(self, fake_bin):
        argv = [
            "/x/gptme",
            "-m",
            "anthropic/claude",
            "--show-hidden",
            "-y",
            "--tools",
            "shell",
            "--allow-hosts",
            "github.com",
            "fix the bug",
        ]
        assert build_restart_argv("tui", "cli", "conv", argv) == [
            str(fake_bin / "gptme-tui"),
            "--no-confirm",
            "--name",
            "conv",
        ]

    def test_tui_to_cli(self, fake_bin):
        argv = ["/x/gptme-tui", "--inline", "--no-confirm", "-v", "-m", "x/y"]
        assert build_restart_argv("cli", "tui", "conv", argv) == [
            str(fake_bin / "gptme"),
            "--no-confirm",
            "--verbose",
            "--name",
            "conv",
        ]

    def test_n_is_name_in_tui(self, fake_bin):
        """`gptme-tui -n NAME`: the value is skipped, not read as a flag."""
        # "-v" here is the *value* of -n (a conversation named "-v")
        argv = ["gptme-tui", "-n", "-v", "--experimental-jelly-errors"]
        assert build_restart_argv("cli", "tui", "conv", argv)[1:] == [
            "--name",
            "conv",
        ]

    def test_n_is_non_interactive_in_cli(self, fake_bin):
        """`gptme -n` is a boolean: the following flag is still parsed."""
        argv = ["gptme", "-n", "-v"]
        assert build_restart_argv("tui", "cli", "conv", argv)[1:] == [
            "--verbose",
            "--name",
            "conv",
        ]

    def test_tui_resume_optional_value(self, fake_bin):
        argv = ["gptme-tui", "-r", "old-conv", "-v"]
        assert build_restart_argv("cli", "tui", "c", argv)[1:] == [
            "--verbose",
            "--name",
            "c",
        ]
        argv = ["gptme-tui", "-r", "--no-confirm"]
        assert build_restart_argv("cli", "tui", "c", argv)[1:] == [
            "--no-confirm",
            "--name",
            "c",
        ]

    def test_combined_short_flags_and_prompts(self, fake_bin):
        argv = ["gptme", "-yv", "--", "-y is a prompt"]
        assert build_restart_argv("tui", "cli", "c", argv)[1:] == [
            "--no-confirm",
            "--verbose",
            "--name",
            "c",
        ]

    def test_falls_back_to_python_m(self, tmp_path, monkeypatch):
        """Without a console script next to the interpreter, use `python -m`."""
        import gptme.tools.restart as mod

        monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
        monkeypatch.setattr(mod, "check_interface_available", lambda t: None)
        assert build_restart_argv("tui", "cli", "c", ["gptme"]) == [
            str(tmp_path / "python"),
            "-m",
            "gptme.tui.main",
            "--name",
            "c",
        ]
        assert build_restart_argv("cli", "tui", "c", ["gptme-tui"])[:3] == [
            str(tmp_path / "python"),
            "-m",
            "gptme",
        ]

    def test_tui_not_installed(self, monkeypatch):
        import importlib.util

        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *a: None if name == "textual" else real_find_spec(name, *a),
        )
        with pytest.raises(RestartError, match=r"gptme\[tui\]"):
            build_restart_argv("tui", "cli", "c", ["gptme"])


class TestDoRestartSwitch:
    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_switch_execs_target_after_cleanup(self, mock_atexit, mock_execv, fake_bin):
        order: list[str] = []
        mock_atexit.side_effect = lambda: order.append("atexit")
        mock_execv.side_effect = lambda *a: order.append("execv")
        with patch.object(sys, "argv", ["gptme", "-y", "hello"]):
            _do_restart("conv", target="tui", source="cli")
        assert order == ["atexit", "execv"]
        exe, args = mock_execv.call_args[0]
        assert exe == str(fake_bin / "gptme-tui")
        assert args == [exe, "--no-confirm", "--name", "conv"]

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_same_target_keeps_command_line(self, mock_atexit, mock_execv):
        with patch.object(sys, "argv", ["gptme-tui", "--inline"]):
            _do_restart("conv", target="tui", source="tui")
        assert mock_execv.call_args[0][1] == [
            "gptme-tui",
            "--inline",
            "--name",
            "conv",
        ]

    @patch("os.execv")
    @patch("atexit._run_exitfuncs")
    def test_unavailable_target_changes_nothing(self, mock_atexit, mock_execv):
        """A failing switch raises before cleanup: the lock stays held."""

        def unavailable(target):
            raise RestartError("no textual")

        with (
            patch("gptme.tools.restart.check_interface_available", unavailable),
            pytest.raises(RestartError),
        ):
            _do_restart("conv", target="tui", source="cli")
        mock_atexit.assert_not_called()
        mock_execv.assert_not_called()


class TestWebSwitch:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        for var in (
            "GPTME_SERVER_URL",
            "GPTME_SERVER_HOST",
            "GPTME_SERVER_PORT",
            "GPTME_SERVER_TOKEN",
        ):
            monkeypatch.delenv(var, raising=False)

    @staticmethod
    def _mock_http(monkeypatch, overrides: dict[str, int | None] | None = None):
        """Mock HTTP: status by URL substring (default 200); records requests."""
        requests: list[tuple[str, str | None]] = []

        def fake(url, token=None, timeout=2.0):
            requests.append((url, token))
            for part, status in (overrides or {}).items():
                if part in url:
                    return status
            return 200

        monkeypatch.setattr("gptme.tools.restart._http_status", fake)
        return requests

    def test_server_url_defaults(self, monkeypatch):
        assert get_server_url() == "http://127.0.0.1:5700"
        monkeypatch.setenv("GPTME_SERVER_PORT", "8080")
        monkeypatch.setenv("GPTME_SERVER_HOST", "0.0.0.0")
        assert get_server_url() == "http://127.0.0.1:8080"
        monkeypatch.setenv("GPTME_SERVER_HOST", "::1")
        assert get_server_url() == "http://[::1]:8080"
        monkeypatch.setenv("GPTME_SERVER_URL", "https://example.test/")
        assert get_server_url() == "https://example.test"

    def test_unreachable(self, monkeypatch):
        self._mock_http(monkeypatch, {"/api/v2/version": None})
        with pytest.raises(RestartError, match="No gptme-server reachable"):
            prepare_web_switch("my-conv")

    def test_http_status_unreachable_returns_none(self):
        # nothing listens on port 9 (discard) on loopback: connection refused
        assert _http_status("http://127.0.0.1:9/", timeout=0.5) is None

    def test_no_webui(self, monkeypatch):
        self._mock_http(monkeypatch, {"/api/v2/version": 200, ":5700/": 503})
        with pytest.raises(RestartError, match="doesn't serve the web UI"):
            prepare_web_switch("my-conv")

    def test_auth_disabled_server_without_token(self, monkeypatch):
        """The server accepted the tokenless request: hand over, selecting it."""
        requests = self._mock_http(monkeypatch)
        url = prepare_web_switch("my-conv")
        assert url == (
            "http://127.0.0.1:5700/chat/my-conv#baseUrl=http%3A%2F%2F127.0.0.1%3A5700"
        )
        assert (
            "http://127.0.0.1:5700/api/v2/conversations/my-conv?limit=1",
            None,
        ) in requests

    def test_auth_required_without_token(self, monkeypatch):
        """Without a token for an auth-enabled server, don't hand over."""
        self._mock_http(monkeypatch, {"/conversations/": 401})
        with pytest.raises(RestartError, match="GPTME_SERVER_TOKEN"):
            prepare_web_switch("my-conv")

    def test_stale_token_rejected(self, monkeypatch):
        monkeypatch.setenv("GPTME_SERVER_TOKEN", "stale")
        self._mock_http(monkeypatch, {"/conversations/": 401})
        with pytest.raises(RestartError, match="rejected GPTME_SERVER_TOKEN"):
            prepare_web_switch("my-conv")

    def test_with_token_verifies_conversation(self, monkeypatch):
        monkeypatch.setenv("GPTME_SERVER_TOKEN", "s3cret")
        requests = self._mock_http(monkeypatch)
        url = prepare_web_switch("my-conv")
        assert url == (
            "http://127.0.0.1:5700/chat/my-conv"
            "#baseUrl=http%3A%2F%2F127.0.0.1%3A5700&userToken=s3cret"
        )
        assert (
            "http://127.0.0.1:5700/api/v2/conversations/my-conv?limit=1",
            "s3cret",
        ) in requests

    def test_conversation_missing_on_server(self, monkeypatch):
        """E.g. GPTME_SERVER_URL points at a server with another logs dir."""
        monkeypatch.setenv("GPTME_SERVER_URL", "http://other:5700")
        self._mock_http(monkeypatch, {"/conversations/my-conv": 404})
        with pytest.raises(RestartError, match="can't find conversation"):
            prepare_web_switch("my-conv")

    def test_conversation_check_unreachable(self, monkeypatch):
        self._mock_http(monkeypatch, {"/conversations/": None})
        with pytest.raises(RestartError, match="couldn't load"):
            prepare_web_switch("my-conv")

    def test_open_web_releases_lock_before_browser(self, monkeypatch, capsys):
        order: list[str] = []
        monkeypatch.setattr("atexit._run_exitfuncs", lambda: order.append("atexit"))

        def fake_open(url: str) -> bool:
            order.append(f"open {url}")
            return True

        monkeypatch.setattr("webbrowser.open", fake_open)
        open_web("http://127.0.0.1:5700/chat/c#userToken=s3cret")
        assert order == [
            "atexit",
            "open http://127.0.0.1:5700/chat/c#userToken=s3cret",
        ]
        out = capsys.readouterr().out
        assert "http://127.0.0.1:5700/chat/c" in out
        assert "s3cret" not in out


class TestCmdRestart:
    """The CLI /restart command with targets."""

    @staticmethod
    def _ctx(tmp_path, args):
        from gptme.commands.base import CommandContext

        manager = MagicMock()
        manager.logdir = tmp_path / "my-conv"
        return CommandContext(args=args, full_args=" ".join(args), manager=manager)

    def test_invalid_target_does_not_prompt(self, tmp_path, capsys):
        from gptme.commands.session import cmd_restart

        with patch("gptme.util.prompt.prompt_alert") as prompt:
            cmd_restart(self._ctx(tmp_path, ["gui"]))
        prompt.assert_not_called()
        assert "Unknown restart target" in capsys.readouterr().out

    def test_web_without_server_does_not_prompt_or_exit(self, tmp_path, capsys):
        from gptme.commands.session import cmd_restart

        with (
            patch("gptme.tools.restart._http_status", return_value=None),
            patch("gptme.util.prompt.prompt_alert") as prompt,
            patch("gptme.tools.restart.open_web") as mock_open_web,
        ):
            cmd_restart(self._ctx(tmp_path, ["web"]))
        prompt.assert_not_called()
        mock_open_web.assert_not_called()
        assert "No gptme-server reachable" in capsys.readouterr().out

    def test_tui_switch(self, tmp_path):
        from gptme.commands.session import cmd_restart

        ctx = self._ctx(tmp_path, ["tui"])
        with (
            patch("gptme.tools.restart.check_interface_available"),
            patch("gptme.util.prompt.prompt_alert", return_value="y"),
            patch("gptme.hooks.trigger_hook", return_value=iter([])) as trigger,
            patch("gptme.tools.restart._do_restart") as do_restart,
        ):
            cmd_restart(ctx)
        ctx.manager.write.assert_called_with(sync=True)
        do_restart.assert_called_once_with("my-conv", target="tui", source="cli")
        trigger.assert_called_once()
        assert trigger.call_args.args[0] == HookType.SESSION_END

    @pytest.mark.parametrize(
        "args",
        [pytest.param([], id="bare"), pytest.param(["cli"], id="cli"), ["tui"]],
    )
    def test_session_end_hooks_run_before_restart(self, tmp_path, args):
        """The CLI restart re-execs, so SESSION_END must run first (as the TUI does)."""
        from gptme.commands.session import cmd_restart

        order = []

        def hook(*_args, **_kwargs):
            order.append("session_end")
            return iter([])

        def do_restart(*_args, **_kwargs):
            order.append("restart")

        with (
            patch("gptme.tools.restart.check_interface_available"),
            patch("gptme.util.prompt.prompt_alert", return_value="y"),
            patch("gptme.hooks.trigger_hook", side_effect=hook),
            patch("gptme.tools.restart._do_restart", side_effect=do_restart),
        ):
            cmd_restart(self._ctx(tmp_path, args))
        assert order == ["session_end", "restart"]

    def test_tui_not_installed_does_not_prompt(self, tmp_path, capsys):
        from gptme.commands.session import cmd_restart

        def unavailable(target):
            raise RestartError("The TUI requires the 'textual' package.")

        with (
            patch("gptme.tools.restart.check_interface_available", unavailable),
            patch("gptme.util.prompt.prompt_alert") as prompt,
            patch("gptme.tools.restart._do_restart") as do_restart,
        ):
            cmd_restart(self._ctx(tmp_path, ["tui"]))
        prompt.assert_not_called()
        do_restart.assert_not_called()
        assert "textual" in capsys.readouterr().out

    def test_cancel(self, tmp_path):
        from gptme.commands.session import cmd_restart

        with (
            patch("gptme.util.prompt.prompt_alert", return_value="n"),
            patch("gptme.tools.restart._do_restart") as do_restart,
        ):
            cmd_restart(self._ctx(tmp_path, []))
        do_restart.assert_not_called()

    def test_web_switch_opens_and_exits(self, tmp_path):
        from gptme.commands.session import cmd_restart

        ctx = self._ctx(tmp_path, ["web"])
        with (
            patch(
                "gptme.tools.restart.prepare_web_switch",
                return_value="http://s/chat/my-conv",
            ),
            patch("gptme.util.prompt.prompt_alert", return_value="y"),
            patch("gptme.hooks.trigger_hook", return_value=iter([])) as trigger,
            patch("gptme.tools.restart.open_web") as mock_open_web,
            patch("gptme.tools.restart._do_restart") as do_restart,
            pytest.raises(SystemExit) as exc,
        ):
            cmd_restart(ctx)
        assert exc.value.code == 0
        mock_open_web.assert_called_once_with("http://s/chat/my-conv")
        do_restart.assert_not_called()
        ctx.manager.write.assert_called_with(sync=True)
        # session-end hooks run exactly once on the web handover
        trigger.assert_called_once()
        assert trigger.call_args.args[0] == HookType.SESSION_END
