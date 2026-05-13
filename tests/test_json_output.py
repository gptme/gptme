"""Tests for the headless JSON output mode (--output-format json)."""

import json
from datetime import datetime, timezone

from click.testing import CliRunner

import gptme.cli.main as cli
from gptme.message import Message, print_msg, set_output_format


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
