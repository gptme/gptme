"""Tests for chats export functionality."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from gptme.logmanager import Log
from gptme.message import Message
from gptme.util.export import export_chat_to_markdown

Role = Literal["system", "user", "assistant"]

_TS = datetime(2026, 3, 7, 12, 0, 0, tzinfo=timezone.utc)


def _make_log(*messages: Message) -> Log:
    """Create a Log from messages."""
    return Log(messages=list(messages))


def _make_msg(role: Role, content: str, **kwargs) -> Message:
    """Create a Message with a fixed timestamp for deterministic tests."""
    return Message(role=role, content=content, timestamp=_TS, **kwargs)


class TestExportMarkdown:
    def test_basic_export(self, tmp_path: Path):
        """Test basic markdown export with user and assistant messages."""
        log = _make_log(
            _make_msg("user", "Hello, how are you?"),
            _make_msg("assistant", "I'm doing well, thanks!"),
        )
        output = tmp_path / "chat.md"
        export_chat_to_markdown("test-chat", log, output)

        content = output.read_text()
        assert "# test-chat" in content
        assert "## User" in content
        assert "## Assistant" in content
        assert "Hello, how are you?" in content
        assert "I'm doing well, thanks!" in content

    def test_timestamp_formatting(self, tmp_path: Path):
        """Test that timestamps are formatted correctly."""
        log = _make_log(_make_msg("user", "Hello"))
        output = tmp_path / "chat.md"
        export_chat_to_markdown("test-chat", log, output)

        content = output.read_text()
        assert "2026-03-07 12:00:00" in content

    def test_hidden_messages_excluded(self, tmp_path: Path):
        """Test that hidden messages are excluded from export."""
        log = _make_log(
            _make_msg("system", "System prompt", hide=True),
            _make_msg("user", "Hello"),
            _make_msg("assistant", "Hi there"),
        )
        output = tmp_path / "chat.md"
        export_chat_to_markdown("test-chat", log, output)

        content = output.read_text()
        assert "System prompt" not in content
        assert "Hello" in content
        assert "Hi there" in content

    def test_system_messages_included(self, tmp_path: Path):
        """Test that non-hidden system messages are included."""
        log = _make_log(
            _make_msg("system", "You are a helpful assistant"),
            _make_msg("user", "Hello"),
        )
        output = tmp_path / "chat.md"
        export_chat_to_markdown("test-chat", log, output)

        content = output.read_text()
        assert "## System" in content
        assert "You are a helpful assistant" in content

    def test_empty_conversation(self, tmp_path: Path):
        """Test export of empty conversation."""
        log = _make_log()
        output = tmp_path / "chat.md"
        export_chat_to_markdown("empty-chat", log, output)

        content = output.read_text()
        assert "# empty-chat" in content
        assert "## User" not in content

    def test_multiline_content(self, tmp_path: Path):
        """Test that multiline content is preserved."""
        code_content = (
            "Here's some code:\n```python\ndef hello():\n    print('hi')\n```"
        )
        log = _make_log(_make_msg("assistant", code_content))
        output = tmp_path / "chat.md"
        export_chat_to_markdown("code-chat", log, output)

        content = output.read_text()
        assert "def hello():" in content
        assert "print('hi')" in content

    def test_role_labels(self, tmp_path: Path):
        """Test that role labels are human-readable."""
        log = _make_log(
            _make_msg("user", "msg1"),
            _make_msg("assistant", "msg2"),
            _make_msg("system", "msg3"),
        )
        output = tmp_path / "chat.md"
        export_chat_to_markdown("test", log, output)

        content = output.read_text()
        assert "## User" in content
        assert "## Assistant" in content
        assert "## System" in content


def _create_test_log(logdir: Path, msgs: list[Message]) -> None:
    """Write messages to a conversation.jsonl file."""
    logfile = logdir / "conversation.jsonl"
    logfile.write_text("".join(json.dumps(msg.to_dict()) + "\n" for msg in msgs))


class TestExportCLI:
    """Test the CLI command for chats export."""

    def test_export_not_found(self, tmp_path: Path, monkeypatch):
        """Test export of non-existent conversation."""
        from click.testing import CliRunner

        from gptme.cli.util import main

        monkeypatch.setattr("gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path)

        runner = CliRunner()
        result = runner.invoke(main, ["chats", "export", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_export_markdown_cli(self, tmp_path: Path, monkeypatch):
        """Test markdown export via CLI."""
        from click.testing import CliRunner

        from gptme.cli.util import main

        logdir = tmp_path / "logs" / "test-conv"
        logdir.mkdir(parents=True)
        _create_test_log(
            logdir,
            [
                Message("user", "Hello", timestamp=_TS),
                Message("assistant", "Hi!", timestamp=_TS),
            ],
        )

        monkeypatch.setattr(
            "gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path / "logs"
        )
        monkeypatch.setattr("gptme.cli.cmd_chats.get_tools", lambda: ["fake"])

        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                main, ["chats", "export", "test-conv", "-f", "markdown"]
            )
            assert result.exit_code == 0, result.output
            assert "Exported conversation to" in result.output

            output_file = Path("test-conv.md")
            assert output_file.exists()
            content = output_file.read_text()
            assert "Hello" in content
            assert "Hi!" in content

    def test_export_html_cli(self, tmp_path: Path, monkeypatch):
        """Test HTML export via CLI (exercises template-reading code path)."""
        from unittest.mock import patch

        from click.testing import CliRunner

        from gptme.cli.util import main

        logdir = tmp_path / "logs" / "test-conv"
        logdir.mkdir(parents=True)
        _create_test_log(
            logdir,
            [
                Message("user", "Hello", timestamp=_TS),
                Message("assistant", "Hi!", timestamp=_TS),
            ],
        )

        monkeypatch.setattr(
            "gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path / "logs"
        )
        monkeypatch.setattr("gptme.cli.cmd_chats.get_tools", lambda: ["fake"])

        # Patch export_chat_to_html to avoid needing real template files in tests
        with patch("gptme.util.export.export_chat_to_html") as mock_export:
            runner = CliRunner()
            with runner.isolated_filesystem():
                result = runner.invoke(
                    main, ["chats", "export", "test-conv", "-f", "html"]
                )
                assert result.exit_code == 0, result.output
                assert "Exported conversation to" in result.output
                assert mock_export.called
                _, _, output_path = mock_export.call_args[0]
                assert str(output_path).endswith(".html")

    def test_export_custom_output(self, tmp_path: Path, monkeypatch):
        """Test export with custom output path."""
        from click.testing import CliRunner

        from gptme.cli.util import main

        logdir = tmp_path / "logs" / "test-conv"
        logdir.mkdir(parents=True)
        _create_test_log(logdir, [Message("user", "Test", timestamp=_TS)])

        monkeypatch.setattr(
            "gptme.cli.cmd_chats.get_logs_dir", lambda: tmp_path / "logs"
        )
        monkeypatch.setattr("gptme.cli.cmd_chats.get_tools", lambda: ["fake"])

        custom_output = tmp_path / "custom-output.md"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["chats", "export", "test-conv", "-o", str(custom_output)],
        )
        assert result.exit_code == 0, result.output
        assert custom_output.exists()
        content = custom_output.read_text()
        assert "Test" in content
