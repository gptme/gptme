from pathlib import Path
from unittest.mock import MagicMock, patch

from gptme.commands.base import CommandContext
from gptme.commands.llm import cmd_context, cmd_model, cmd_tools
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


def test_cmd_model_appends_replacement_prompt_and_persists_generation(tmp_path: Path):
    manager = _make_manager(tmp_path)
    ctx = CommandContext(args=["new-model"], full_args="new-model", manager=manager)
    replacement = Message(
        "system",
        "replacement",
        metadata={"prompt_generation": "replacement-id"},
    )
    model_meta = MagicMock(full="provider/new-model", default_tool_format="tool")

    with (
        patch("gptme.config.ChatConfig.from_logdir") as from_logdir,
        patch("gptme.llm.models.set_default_model"),
        patch("gptme.llm.models.get_default_model", return_value=model_meta),
        patch("gptme.commands.llm._replacement_prompt", return_value=[replacement]),
        patch("gptme.tools.base.set_tool_format"),
    ):
        chat_config = from_logdir.return_value
        chat_config.interactive = True
        chat_config.workspace = tmp_path
        chat_config.agent = None

        yielded = list(cmd_model(ctx))

    assert yielded == [replacement]
    assert chat_config.model == "new-model"
    assert chat_config.tool_format == "tool"
    chat_config.save.assert_called_once_with()


def test_cmd_tools_load_appends_replacement_prompt(tmp_path: Path):
    manager = _make_manager(tmp_path)
    ctx = CommandContext(
        args=["load", "python"], full_args="load python", manager=manager
    )
    replacement = Message(
        "system",
        "replacement",
        metadata={"prompt_generation": "replacement-id"},
    )
    model_meta = MagicMock(full="provider/model")

    with (
        patch("gptme.config.ChatConfig.from_logdir") as from_logdir,
        patch("gptme.llm.models.get_default_model", return_value=model_meta),
        patch("gptme.tools.load_tool"),
        patch("gptme.commands.llm._replacement_prompt", return_value=[replacement]),
    ):
        yielded = list(cmd_tools(ctx))

    assert yielded == [replacement]
    from_logdir.assert_called_once_with(tmp_path)
    from_logdir.return_value.save.assert_called_once_with()


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
