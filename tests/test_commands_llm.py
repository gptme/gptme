from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.commands.base import CommandContext
from gptme.commands.llm import cmd_context
from gptme.message import Message
from gptme.util.context_savings import record_context_savings


def _make_manager(logdir: Path) -> MagicMock:
    manager = MagicMock()
    manager.log = MagicMock()
    manager.log.messages = [
        Message("user", "hello"),
        Message("assistant", "world"),
    ]
    manager.logdir = logdir
    return manager


def test_cmd_context_reports_context_savings(tmp_path: Path):
    record_context_savings(
        logdir=tmp_path,
        source="shell",
        original_tokens=1200,
        kept_tokens=300,
        command_info="git log --oneline",
        saved_path=tmp_path / "tool-outputs" / "shell" / "saved.txt",
    )
    ctx = CommandContext(args=[], full_args="", manager=_make_manager(tmp_path))

    with (
        patch("gptme.llm.models.get_default_model", return_value=None),
        patch("gptme.util.console.log") as mock_log,
    ):
        cmd_context(ctx)

    output = "\n".join(str(call.args[0]) for call in mock_log.call_args_list)
    assert "Context Savings" in output
    assert "900" in output
    assert "shell" in output
