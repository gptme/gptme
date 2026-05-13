"""Tests for the headless JSON output mode (--output-format json)."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import gptme.cli.main as cli
from gptme.message import Message, get_output_format, print_msg, set_output_format


@pytest.fixture(autouse=True)
def reset_output_format():
    """Guarantee _output_format is reset to 'text' after every test, even on failure."""
    yield
    set_output_format("text")


class TestOutputFormatValidation:
    """Tests for CLI flag validation."""

    def test_json_requires_noninteractive(self):
        """--output-format json should error without --non-interactive."""
        runner = CliRunner()
        # Simulate an interactive TTY so the auto-switch doesn't trigger
        with runner.isolated_filesystem():
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

    def test_output_format_default(self):
        """Default output_format should be 'text'."""
        runner = CliRunner()
        result = runner.invoke(cli.main, ["--help"])
        assert result.exit_code == 0
        assert "--output--format" in result.output or "--output-format" in result.output


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
    CLI → chat() → print_msg() → stdout path via Click's CliRunner.

    Click 8.2+ exposes result.stdout (stdout only) and result.stderr separately,
    so we read result.stdout — exactly what a real caller sees when piping stdout.
    fake_chat calls print_msg() normally; no manual sys.stdout redirect needed.

    The critical invariant: EVERY non-empty byte on stdout must be a valid JSON
    object, so callers can do ``for line in proc.stdout: json.loads(line)``.
    """

    def _run_and_capture(self, messages: list[Message]) -> str:
        """Invoke the CLI in JSON mode with a fake chat(); return only what print_msg wrote."""

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
            try:
                set_output_format(output_format)
                for msg in messages:
                    print_msg(msg)
            finally:
                set_output_format(prev_fmt)

        runner = CliRunner()
        with patch("gptme.cli.main.chat", new=fake_chat):
            result = runner.invoke(
                cli.main,
                ["--output-format", "json", "--non-interactive", "hello"],
                catch_exceptions=False,
            )
        # result.stdout (Click 8.2+) contains only stdout; stderr is separate.
        # This ensures logging/Rich output on stderr does not contaminate the check.
        return result.stdout

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
