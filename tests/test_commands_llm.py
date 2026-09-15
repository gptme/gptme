from contextvars import ContextVar
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        patch("gptme.tools.load_tool") as load_tool,
        patch("gptme.commands.llm._replacement_prompt", return_value=[replacement]),
    ):
        yielded = list(cmd_tools(ctx))

    assert yielded == [replacement]
    load_tool.assert_called_once_with("python", allow_required=True)
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


@pytest.mark.parametrize("command", ["model", "tools"])
@pytest.mark.parametrize("change", ["replace", "remove", "disable"])
def test_commands_refresh_runtime_fragments_from_live_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, change: str
) -> None:
    from gptme.config import ChatConfig, Config, get_config
    from gptme.logmanager import LogManager
    from gptme.logmanager.manager import _active_prompt_generation
    from gptme.prompts import get_prompt
    from gptme.tools import clear_tools, get_tools, init_tools

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    main = config_dir / "config.toml"
    main.write_text('[prompt.fragments]\nuser = "User instructions."\n')
    local = config_dir / "config.local.toml"
    local.write_text('[prompt.fragments]\nlocal = "Local instructions."\n')
    runtime = config_dir / "config.runtime.toml"
    runtime.write_text('[prompt.fragments]\npreview = "Old deployment guidance."\n')
    source_bytes = {path: path.read_bytes() for path in (main, local)}
    monkeypatch.setattr("gptme.config.user.config_path", str(main))
    monkeypatch.setattr("gptme.prompts.workspace.config_path", str(main))
    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path / "logs"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "gptme.toml").write_text('prompt = "Project instructions."\n')
    logdir = tmp_path / "logs" / "live-regeneration"
    chat = ChatConfig(
        _logdir=logdir,
        workspace=workspace,
        system_prompt="Custom conversation instructions.",
        model="local/test",
        tools=["save"],
        env={"SESSION_SETTING": "preserved"},
    )
    chat.save()
    cached = Config.from_logdir(logdir)
    monkeypatch.setattr(
        "gptme.config.core._config_var", ContextVar("live_config", default=cached)
    )
    clear_tools()
    init_tools(["save"])
    original_tool = get_tools()[0]
    initial = get_prompt(
        get_tools(),
        prompt=chat.system_prompt or "full",
        model="local/test",
        prompt_generation="initial",
    )
    manager = LogManager(initial, logdir=logdir, lock=False)
    assert cached.user.prompt.fragments["preview"] == "Old deployment guidance."

    if change == "replace":
        replacement = config_dir / "runtime.next"
        replacement.write_text(
            '[prompt.fragments]\npreview = "Updated deployment guidance."\n'
        )
        replacement.replace(runtime)
    elif change == "remove":
        runtime.unlink()
    else:
        local.write_text(local.read_text() + 'preview = ""\n')
        source_bytes[local] = local.read_bytes()
    runtime_bytes = runtime.read_bytes() if runtime.exists() else None
    assert get_config() is cached
    assert cached.user.prompt.fragments["preview"] == "Old deployment guidance."

    args = ["local/test"] if command == "model" else ["load", "ipython"]
    ctx = CommandContext(args=args, full_args=" ".join(args), manager=manager)
    generated = list(cmd_model(ctx) if command == "model" else cmd_tools(ctx))
    content = "\n".join(message.content for message in generated)
    assert "Old deployment guidance." not in content
    assert ("Updated deployment guidance." in content) == (change == "replace")
    for preserved in (
        "User instructions.",
        "Local instructions.",
        "Custom conversation instructions.",
    ):
        assert content.count(preserved) == 1
    assert get_config().chat is cached.chat
    assert get_config().project is cached.project
    assert get_config().get_env("SESSION_SETTING") == "preserved"
    assert get_tools()[0] is original_tool
    if command == "tools":
        assert [tool.name for tool in get_tools()] == ["save", "ipython"]
        assert "ipython" in content
        assert ChatConfig.from_logdir(logdir).tools == ["save", "ipython"]
    assert ChatConfig.from_logdir(logdir).system_prompt == chat.system_prompt
    assert cached.user.prompt.fragments["preview"] == "Old deployment guidance."
    active = _active_prompt_generation(initial + generated)
    assert "Old deployment guidance." not in "\n".join(m.content for m in active)
    assert all(path.read_bytes() == value for path, value in source_bytes.items())
    assert (runtime.read_bytes() if runtime.exists() else None) == runtime_bytes
