"""Tests for the headless JSON output mode (--output-format json)."""

import io
import json
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli
import gptme.init as gptme_init
import gptme.llm as llm
from gptme.message import Message, get_output_format, print_msg, set_output_format


@pytest.fixture(autouse=True)
def reset_output_format():
    """Guarantee _output_format is reset to 'text' after every test, even on failure."""
    yield
    set_output_format("text")


def _invoke_cli_with_captured_goodbye(monkeypatch, tmp_path: Path, args: list[str]):
    """Run the CLI while capturing the registered goodbye handler."""
    handlers: list[Any] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli.atexit, "register", handlers.append)

    # Pre-chat mocks to make tests environment-independent.
    # Without these the full setup_config_from_cli → init_tools → get_prompt
    # pipeline runs in a bare tmp_path with no model configured, so the CLI
    # exits through UsageError before reaching chat().
    fake_config = SimpleNamespace(
        chat=SimpleNamespace(
            agent_config=None,
            tools=[],
            interactive=False,
            tool_format="markdown",
            model="local/test",
            workspace=tmp_path,
            stream=False,
            agent=None,
            gear=None,
            no_confirm=None,
        ),
        project=None,
    )
    monkeypatch.setattr(cli, "setup_config_from_cli", lambda **_: fake_config)
    monkeypatch.setattr(cli, "init_tools", lambda _: [])
    monkeypatch.setattr(cli, "get_prompt", lambda **_: [])
    monkeypatch.setattr(cli, "init_telemetry", lambda **_: None)
    monkeypatch.setattr(cli, "set_interruptible", lambda: None)
    monkeypatch.setattr(cli.signal, "signal", lambda *args, **kwargs: None)

    runner = CliRunner()
    result = runner.invoke(cli.main, args, input="")
    goodbye_handler = next(
        handler
        for handler in handlers
        if getattr(handler, "__name__", "") == "goodbye_handler"
    )
    return result, goodbye_handler


class TestOutputFormatValidation:
    """Tests for CLI flag validation."""

    def test_json_requires_noninteractive(self, monkeypatch):
        """--output-format json should error without --non-interactive."""
        runner = CliRunner()
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        # Simulate a real interactive TTY so the auto-switch path stays off.
        with (
            patch("click.testing._NamedTextIOWrapper.isatty", return_value=True),
            runner.isolated_filesystem(),
        ):
            result = runner.invoke(
                cli.main,
                ["--output-format", "json", "/exit"],
                input="",
            )
        assert result.exit_code != 0
        assert "only allowed with" in result.output.lower() or (
            result.exception is not None
            and "only allowed" in str(result.exception).lower()
        )

    def test_json_with_noninteractive_parses(self):
        """--output-format json --non-interactive should fail about missing prompt, not output-format."""
        runner = CliRunner()
        # Omit the prompt so the CLI exits fast with "requires a prompt" — no API call needed.
        result = runner.invoke(
            cli.main, ["--output-format", "json", "--non-interactive"]
        )
        # Should fail (no prompt given), but the error must not mention --output-format
        output = (result.output or "").lower()
        exc_str = str(result.exception or "").lower()
        assert "output-format" not in output, (
            f"Unexpected output-format error in output: {result.output}"
        )
        assert "output-format" not in exc_str, (
            f"Unexpected output-format error in exception: {result.exception}"
        )

    def test_json_auto_switched_headless_prompt_is_allowed(
        self, monkeypatch, tmp_path: Path
    ):
        """A prompt on non-TTY stdin auto-switches to headless mode and should allow JSON."""
        received: list[tuple[bool, str]] = []

        fake_config = SimpleNamespace(
            chat=SimpleNamespace(
                agent_config=None,
                tools=[],
                interactive=False,
                tool_format="markdown",
                model="local/test",
                workspace=tmp_path,
                stream=False,
                agent=None,
                gear=None,
                no_confirm=None,
            ),
            project=None,
        )

        def fake_chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            workspace,
            model,
            stream=True,
            no_confirm=False,
            interactive=True,
            show_hidden=False,
            tool_allowlist=None,
            tool_format=None,
            output_schema=None,
            output_format="text",
        ):
            received.append((interactive, output_format))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setattr(cli, "setup_config_from_cli", lambda **_: fake_config)
        monkeypatch.setattr(cli, "init_tools", lambda _: [])
        monkeypatch.setattr(cli, "get_prompt", lambda **_: [])
        monkeypatch.setattr(cli, "init_telemetry", lambda **_: None)
        monkeypatch.setattr(cli, "set_interruptible", lambda: None)
        monkeypatch.setattr(cli.signal, "signal", lambda *args, **kwargs: None)

        runner = CliRunner()
        with patch("gptme.cli.main.chat", new=fake_chat):
            result = runner.invoke(
                cli.main,
                ["--output-format", "json", "hello"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        assert received == [(False, "json")]

    def test_json_resume_error_keeps_stdout_clean(self, monkeypatch, tmp_path):
        """JSON mode must not leak Rich logs onto stdout on early resume errors."""
        runner_cls: Any = CliRunner
        try:
            runner = runner_cls(mix_stderr=False)
        except TypeError:
            runner = runner_cls()
        result = runner.invoke(
            cli.main,
            ["--output-format", "json", "--non-interactive", "--resume"],
            env={
                "HOME": str(tmp_path),
                "XDG_DATA_HOME": str(tmp_path),
                "XDG_STATE_HOME": str(tmp_path / "state"),
            },
        )

        assert result.exit_code == 2
        assert result.stdout.strip() == "", (
            "stdout must stay empty on early JSON-mode errors so supervisors don't "
            "see non-JSON bytes before the process exits"
        )
        assert "No previous conversations to resume" in result.stderr

    def test_json_missing_explicit_path_prompt_keeps_stdout_clean(self, tmp_path):
        """A missing explicit path prompt must fail before any JSON-mode stdout output."""
        runner_cls: Any = CliRunner
        try:
            runner = runner_cls(mix_stderr=False)
        except TypeError:
            runner = runner_cls()
        missing_path = tmp_path / "missing-prompt.txt"
        result = runner.invoke(
            cli.main,
            ["--output-format", "json", "--non-interactive", str(missing_path)],
            env={
                "HOME": str(tmp_path),
                "XDG_DATA_HOME": str(tmp_path),
                "XDG_STATE_HOME": str(tmp_path / "state"),
            },
        )

        assert result.exit_code == 2
        assert result.stdout.strip() == "", (
            "stdout must stay empty on early JSON-mode prompt validation errors"
        )
        assert "explicit local path" in result.stderr
        assert str(missing_path) in result.stderr

    def test_json_missing_explicit_path_with_other_prompt_keeps_stdout_clean(
        self, tmp_path
    ):
        """Mixed prompt/path argv should still fail before any JSON stdout output."""
        runner_cls: Any = CliRunner
        try:
            runner = runner_cls(mix_stderr=False)
        except TypeError:
            runner = runner_cls()
        missing_path = tmp_path / "missing-prompt.txt"
        result = runner.invoke(
            cli.main,
            [
                "--output-format",
                "json",
                "--non-interactive",
                str(missing_path),
                "hello",
            ],
            env={
                "HOME": str(tmp_path),
                "XDG_DATA_HOME": str(tmp_path),
                "XDG_STATE_HOME": str(tmp_path / "state"),
            },
        )

        assert result.exit_code == 2
        assert result.stdout.strip() == "", (
            "stdout must stay empty on early JSON-mode prompt validation errors"
        )
        assert "explicit local path" in result.stderr
        assert str(missing_path) in result.stderr

    def test_noninteractive_missing_prompt_has_no_fake_resume_hint(
        self, monkeypatch, tmp_path: Path
    ):
        """A validation error before chat start must not print a bogus resume hint."""
        result, goodbye_handler = _invoke_cli_with_captured_goodbye(
            monkeypatch, tmp_path, ["--non-interactive", "--name", "missing-prompt"]
        )

        goodbye_output = io.StringIO()
        with redirect_stdout(goodbye_output):
            goodbye_handler()

        output = (result.output or "").lower()
        goodbye_text = goodbye_output.getvalue().lower()
        assert result.exit_code != 0
        assert "requires a prompt" in output
        assert "resume with:" not in goodbye_text
        assert "goodbye!" not in goodbye_text

    def test_goodbye_handler_prints_resume_hint_for_existing_conversation(
        self, monkeypatch, tmp_path: Path
    ):
        """The goodbye handler should print once a conversation log exists."""

        def fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
            (logdir / "conversation.jsonl").write_text(
                '{"role":"user","content":"hello"}\n'
            )

        monkeypatch.setattr(cli, "chat", fake_chat)
        result, goodbye_handler = _invoke_cli_with_captured_goodbye(
            monkeypatch,
            tmp_path,
            ["--non-interactive", "--name", "resume-test", "hello"],
        )

        goodbye_output = io.StringIO()
        with redirect_stdout(goodbye_output):
            goodbye_handler()

        assert result.exit_code == 0
        assert "resume with: gptme --name resume-test" in goodbye_output.getvalue()

    def test_fatal_chat_error_has_no_fake_resume_hint(
        self, monkeypatch, tmp_path: Path
    ):
        """Fatal chat errors must not advertise a resume hint for the failed run."""

        def fake_chat(prompt_msgs, initial_msgs, logdir, *args, **kwargs):
            (logdir / "conversation.jsonl").write_text(
                '{"role":"user","content":"hello"}\n'
            )
            raise RuntimeError(f"Another gptme instance is using {logdir}")

        monkeypatch.setattr(cli, "chat", fake_chat)
        result, goodbye_handler = _invoke_cli_with_captured_goodbye(
            monkeypatch,
            tmp_path,
            ["--non-interactive", "--name", "fatal-resume", "hello"],
        )

        goodbye_output = io.StringIO()
        with redirect_stdout(goodbye_output):
            goodbye_handler()

        output = (result.output or "").lower()
        goodbye_text = goodbye_output.getvalue().lower()
        assert result.exit_code != 0
        assert "fatal error occurred" in output
        assert "another gptme instance is using" in output
        assert "resume with:" not in goodbye_text
        assert "goodbye!" not in goodbye_text

    def test_should_print_resume_hint_handles_missing_conversation_log(
        self, tmp_path: Path
    ):
        """A missing conversation log should be treated as no resumable chat."""
        assert cli._should_print_resume_hint(tmp_path, "text") is False

    def test_output_format_default(self):
        """Default output_format should be 'text'."""
        runner = CliRunner()
        result = runner.invoke(cli.main, ["--help"])
        assert result.exit_code == 0
        assert "--output--format" in result.output or "--output-format" in result.output


class TestJSONRuntimeSuppression:
    """Runtime output in JSON mode must stay off the human-readable stdout rail.

    Uses capfd (capture file descriptors) instead of capsys because Rich's
    Console/rprint hold a reference to the real sys.stdout at import time, so
    capsys (which redirects the Python-level sys.stdout object) cannot intercept
    their output. capfd captures at the OS file-descriptor level and catches
    everything.
    """

    def test_init_model_suppresses_model_banner_in_json_mode(self, monkeypatch, capfd):
        set_output_format("json")
        monkeypatch.setattr(gptme_init, "init_llm", lambda _provider: None)
        monkeypatch.setattr(gptme_init, "set_default_model", lambda _model: None)

        gptme_init.init_model("openai/gpt-4o-mini", interactive=False)

        captured = capfd.readouterr()
        assert captured.out == ""

    def test_guess_provider_suppresses_banner_in_json_mode(self, monkeypatch, capfd):
        set_output_format("json")
        monkeypatch.setattr(
            llm,
            "list_available_providers",
            lambda: [("openai", "OPENAI_API_KEY")],
        )

        provider = llm.guess_provider_from_config()

        captured = capfd.readouterr()
        assert provider == "openai"
        assert captured.out == ""

    def test_init_model_suppresses_no_api_keys_warning_in_json_mode(
        self, monkeypatch, capfd
    ):
        """Init model path where no API keys are set must NOT leak the
        'No API keys set' warning to stdout when --output-format json."""
        set_output_format("json")
        mock_config = SimpleNamespace(
            chat=None,
            user=SimpleNamespace(models=SimpleNamespace(default=None)),
            get_env=lambda key, default=None: None,
        )
        monkeypatch.setattr(gptme_init, "get_config", lambda: mock_config)
        # Must patch on gptme_init, not llm — init.py imports via `from .llm import ...`,
        # which creates a module-level global that LOAD_GLOBAL resolves from gptme_init.
        monkeypatch.setattr(gptme_init, "guess_provider_from_config", lambda: None)
        monkeypatch.setattr(gptme_init, "init_llm", lambda _provider: None)
        monkeypatch.setattr(gptme_init, "set_default_model", lambda _model: None)

        # init_model raises ValueError when no keys are available
        with pytest.raises(ValueError, match="No API key found"):
            gptme_init.init_model(None, interactive=False)

        captured = capfd.readouterr()
        # In JSON mode, the warning must NOT leak to stdout
        assert "No API keys set" not in captured.out

    def test_streaming_reply_suppresses_progress_in_json_mode(self, monkeypatch, capfd):
        set_output_format("json")

        def fake_stream():
            yield "OK"
            return {"model": "openai/gpt-4o-mini"}

        monkeypatch.setattr(
            llm,
            "_stream",
            lambda *args, **kwargs: llm._StreamWithMetadata(
                fake_stream(), "openai/gpt-4o-mini"
            ),
        )
        monkeypatch.setattr(llm, "len_tokens", lambda *_args, **_kwargs: 1)

        msg = llm._reply_stream(
            [Message("user", "hello")],
            "openai/gpt-4o-mini",
            tools=None,
            break_on_tooluse=False,
            agent_name="bob",
        )

        captured = capfd.readouterr()
        assert msg.content == "OK"
        assert captured.out == ""

    def test_nonstream_reply_suppresses_progress_in_json_mode(self, monkeypatch, capfd):
        set_output_format("json")
        monkeypatch.setattr(llm, "init_llm", lambda _provider: None)
        monkeypatch.setattr(
            llm,
            "_chat_complete",
            lambda *args, **kwargs: ("OK", {"model": "openai/gpt-4o-mini"}),
        )

        msg = llm.reply(
            [Message("user", "hello")],
            model="openai/gpt-4o-mini",
            tools=None,
            stream=False,
        )

        captured = capfd.readouterr()
        assert msg.content == "OK"
        assert captured.out == ""


class TestJSONRendering:
    """Tests for JSON rendering of print_msg."""

    def test_json_renders_message(self, capsys):
        """print_msg should emit JSONL in JSON mode."""
        set_output_format("json")
        msg = Message("user", "hello world")
        print_msg(msg)
        set_output_format("text")  # reset

        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) >= 1
        event = json.loads(lines[0])
        assert event["type"] == "message"
        assert event["role"] == "user"
        assert event["content"] == "hello world"

    def test_json_hides_hidden_messages(self, capsys):
        """Hidden messages should be skipped in JSON mode by default."""
        set_output_format("json")
        msg = Message("system", "hidden message", hide=True)
        print_msg(msg)
        set_output_format("text")

        captured = capsys.readouterr()
        assert not captured.out.strip(), (
            "Hidden message should not appear in JSON output"
        )

    def test_json_renders_assistant_message(self, capsys):
        """Assistant messages should render correctly in JSON mode."""
        from datetime import datetime

        set_output_format("json")
        ts = datetime.now(timezone.utc)
        msg = Message(
            "assistant",
            "I am an AI assistant.",
            timestamp=ts,
        )
        print_msg(msg)
        set_output_format("text")

        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert event["type"] == "message"
        assert event["role"] == "assistant"
        assert event["content"] == "I am an AI assistant."
        assert event["timestamp"] == ts.isoformat()

    def test_json_output_has_timestamp(self, capsys):
        """JSON output should include ISO-formatted timestamps."""
        set_output_format("json")
        msg = Message("user", "test")
        print_msg(msg)
        set_output_format("text")

        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert "timestamp" in event
        # Verify it's a valid ISO format
        datetime.fromisoformat(event["timestamp"])

    def test_json_supports_metadata(self, capsys):
        """Messages with metadata should include it in JSON output."""
        set_output_format("json")
        msg = Message(
            "assistant",
            "response",
            metadata={"model": "test-model", "cost": 0.001},
        )
        print_msg(msg)
        set_output_format("text")

        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert event["metadata"]["model"] == "test-model"
        assert event["metadata"]["cost"] == 0.001

    def test_nested_chat_restores_parent_format(self):
        """Nested chat() calls (inline subagents) must restore parent's format on exit.

        Regression test for the reentrancy bug: a subagent calling chat() with
        output_format="text" used to unconditionally reset the global to "text",
        silently corrupting the parent's JSONL stream for all subsequent messages.

        Verifies get_output_format() + set_output_format() save/restore works.
        """

        def simulate_chat(output_format: str) -> None:
            """Mirrors the save/restore pattern now used in chat()."""
            prev = get_output_format()
            set_output_format(output_format)
            try:
                pass  # body of chat would go here
            finally:
                set_output_format(prev)

        # Parent enters JSON mode
        set_output_format("json")
        assert get_output_format() == "json"

        # Inline subagent calls chat() with default output_format="text"
        simulate_chat("text")

        # Parent's JSON mode must be intact after subagent returns
        assert get_output_format() == "json", (
            "parent's JSON format must be restored after nested chat() returns"
        )

    def test_setup_exception_restores_format(self):
        """If an exception occurs during chat() setup, format must be restored.

        Regression test for the gap where set_output_format() was called before
        the try/finally block, leaving _output_format stuck in 'json' mode if
        init(), get_model(), LogManager.load(), or os.chdir() raised.
        """

        def simulate_chat_setup_raises(output_format: str) -> None:
            """Mirrors the expanded try/finally structure now in chat()."""
            prev = get_output_format()
            try:
                set_output_format(output_format)
                raise RuntimeError("simulated setup failure (e.g. model not found)")
            finally:
                set_output_format(prev)

        set_output_format("json")
        with pytest.raises(RuntimeError, match="simulated setup failure"):
            simulate_chat_setup_raises("json")
        assert get_output_format() == "json", (
            "format must be restored to caller's value after setup exception"
        )

    def test_json_multiple_messages(self, capsys):
        """Multiple messages should each emit a separate JSON line."""
        set_output_format("json")
        msgs = [
            Message("user", "first"),
            Message("assistant", "second"),
        ]
        print_msg(msgs)
        set_output_format("text")

        captured = capsys.readouterr()
        lines = [line for line in captured.out.strip().split("\n") if line]
        assert len(lines) >= 2
        json.loads(lines[0])  # no error
        json.loads(lines[1])  # no error
        assert json.loads(lines[0])["content"] == "first"
        assert json.loads(lines[1])["content"] == "second"


class TestJSONOutputIntegration:
    """End-to-end tests validating stdout is pure JSONL when --output-format json is used.

    These tests mock chat() to avoid API calls while still exercising the full
    CLI → chat() → print_msg() → stdout path.

    Design note: CliRunner (Click's test helper) merges stdout and stderr into
    result.output, which would mix Rich log lines with our JSON output. To test
    stdout in isolation we temporarily redirect sys.stdout inside the fake chat()
    to a separate StringIO buffer; that buffer captures exactly what print_msg()
    writes, independent of logging.

    The critical invariant: EVERY non-empty byte on stdout must be a valid JSON
    object, so callers can do ``for line in proc.stdout: json.loads(line)``.
    """

    def _run_and_capture(self, messages: list[Message]) -> str:
        """Invoke the CLI in JSON mode with a fake chat(); return only what print_msg wrote."""
        json_out = io.StringIO()

        def fake_chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            workspace,
            model,
            stream=True,
            no_confirm=False,
            interactive=True,
            show_hidden=False,
            tool_allowlist=None,
            tool_format=None,
            output_schema=None,
            output_format="text",
        ):
            prev_fmt = get_output_format()
            # Redirect sys.stdout so print_msg writes to our isolated buffer,
            # separate from CliRunner's merged stdout+stderr stream.
            old_stdout, sys.stdout = sys.stdout, json_out
            try:
                set_output_format(output_format)
                for msg in messages:
                    print_msg(msg)
            finally:
                sys.stdout = old_stdout
                set_output_format(prev_fmt)

        runner = CliRunner()
        with patch("gptme.cli.main.chat", new=fake_chat):
            runner.invoke(
                cli.main,
                ["--output-format", "json", "--non-interactive", "hello"],
                catch_exceptions=False,
            )
        return json_out.getvalue()

    def _assert_pure_jsonl(self, stdout: str, *, min_lines: int = 1) -> list[dict]:
        """Assert every non-empty stdout line is a valid JSON object; return parsed list."""
        lines = [line for line in stdout.splitlines() if line.strip()]
        assert len(lines) >= min_lines, (
            f"Expected at least {min_lines} JSON line(s) on stdout, got {len(lines)}.\n"
            f"Full stdout:\n{stdout!r}"
        )
        objects = []
        for i, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                pytest.fail(
                    f"Non-JSON on stdout at line {i} — stdout must be pure JSONL.\n"
                    f"Offending line: {line!r}\n"
                    f"JSONDecodeError: {exc}\n"
                    f"Full stdout:\n{stdout!r}"
                )
            assert isinstance(obj, dict), (
                f"Expected a JSON object on line {i}, got {type(obj).__name__}: {line!r}"
            )
            objects.append(obj)
        return objects

    def _invoke_real_json_cli(self, tmp_path: Path, prompt: str):
        """Invoke the real CLI in JSON mode with isolated data dirs and split stdout/stderr."""
        runner_cls: Any = CliRunner
        try:
            runner = runner_cls(mix_stderr=False)
        except TypeError:
            runner = runner_cls()
        return runner.invoke(
            cli.main,
            ["--output-format", "json", "--non-interactive", prompt],
            env={
                "HOME": str(tmp_path),
                "XDG_DATA_HOME": str(tmp_path),
                "XDG_STATE_HOME": str(tmp_path / "state"),
            },
        )

    def test_stdout_is_pure_jsonl(self):
        """Core invariant: stdout contains ONLY valid JSON objects, zero non-JSON lines."""
        messages = [
            Message("user", "hello"),
            Message("assistant", "world"),
        ]
        stdout = self._run_and_capture(messages)
        objects = self._assert_pure_jsonl(stdout, min_lines=2)
        assert objects[0]["role"] == "user"
        assert objects[0]["content"] == "hello"
        assert objects[1]["role"] == "assistant"
        assert objects[1]["content"] == "world"

    def test_stdout_jsonl_is_machine_readable(self):
        """Stdout is consumable by a JSONL reader: every line parses, schema is correct."""
        messages = [Message("assistant", f"message {i}") for i in range(5)]
        stdout = self._run_and_capture(messages)
        objects = self._assert_pure_jsonl(stdout, min_lines=5)
        for i, obj in enumerate(objects):
            assert obj["role"] == "assistant"
            assert obj["content"] == f"message {i}"
            assert obj["type"] == "message"
            assert "timestamp" in obj
            datetime.fromisoformat(obj["timestamp"])  # must be valid ISO 8601

    def test_stdout_has_no_rich_markup(self):
        """No Rich/ANSI escape codes or prose text leak into the JSONL stream."""
        messages = [Message("assistant", "clean output")]
        stdout = self._run_and_capture(messages)
        self._assert_pure_jsonl(stdout, min_lines=1)
        assert "\x1b[" not in stdout, "ANSI escape codes leaked into JSONL stdout"

    def test_cli_passes_json_format_to_chat(self):
        """The CLI must forward output_format='json' to chat() when --output-format json is given."""
        received_format: list[str] = []

        def fake_chat(
            prompt_msgs,
            initial_msgs,
            logdir,
            workspace,
            model,
            stream=True,
            no_confirm=False,
            interactive=True,
            show_hidden=False,
            tool_allowlist=None,
            tool_format=None,
            output_schema=None,
            output_format="text",
        ):
            received_format.append(output_format)

        runner = CliRunner()
        with patch("gptme.cli.main.chat", new=fake_chat):
            runner.invoke(
                cli.main,
                ["--output-format", "json", "--non-interactive", "hello"],
                catch_exceptions=False,
            )
        assert received_format == ["json"], (
            f"CLI must pass output_format='json' to chat(); got {received_format}"
        )

    def test_help_command_stdout_stays_jsonl(self, tmp_path: Path):
        """Slash commands must not leak plain text onto stdout in JSON mode."""
        result = self._invoke_real_json_cli(tmp_path, "/help")

        assert result.exit_code == 0, result.stderr
        objects = self._assert_pure_jsonl(result.stdout, min_lines=2)
        assert objects[0]["role"] == "user"
        assert objects[0]["content"] == "/help"
        assert objects[-1]["role"] == "assistant"
        assert "Available commands:" in objects[-1]["content"]
        assert "Keyboard shortcuts:" in objects[-1]["content"]

    def test_tokens_command_stdout_stays_jsonl(self, tmp_path: Path):
        """Rich console command output must also stay on the JSONL rail."""
        result = self._invoke_real_json_cli(tmp_path, "/tokens")

        assert result.exit_code == 0, result.stderr
        objects = self._assert_pure_jsonl(result.stdout, min_lines=2)
        assert objects[0]["content"] == "/tokens"
        assert objects[-1]["role"] == "assistant"
        assert "No cost data available" in objects[-1]["content"]

    def test_impersonate_command_stdout_stays_jsonl(self, tmp_path: Path):
        """Commands that yield Messages must still emit those messages directly in JSON mode."""
        result = self._invoke_real_json_cli(tmp_path, "/impersonate hello")

        assert result.exit_code == 0, result.stderr
        objects = self._assert_pure_jsonl(result.stdout, min_lines=2)
        assert len(objects) == 2
        assert objects[0]["content"] == "/impersonate hello"
        assert objects[1]["role"] == "assistant"
        assert objects[1]["content"] == "hello"

    def test_unknown_command_stdout_stays_jsonl(self, tmp_path: Path):
        """Unknown commands should also stay on the JSONL rail."""
        result = self._invoke_real_json_cli(tmp_path, "/definitely-not-a-command")

        assert result.exit_code == 0, result.stderr
        objects = self._assert_pure_jsonl(result.stdout, min_lines=2)
        assert len(objects) == 2
        assert objects[0]["content"] == "/definitely-not-a-command"
        assert objects[1]["role"] == "assistant"
        assert (
            objects[1]["content"]
            == "Unknown command. Use /help to see available commands."
        )
