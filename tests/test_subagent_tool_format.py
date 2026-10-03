"""Native children select their own model's dialect, not their parent's."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest

from gptme.llm.models import ModelMeta
from gptme.tools.subagent import execution


@pytest.mark.parametrize("dialect", ["markdown", "xml", "tool"])
@pytest.mark.parametrize(
    ("context_mode", "context_window"),
    [("full", None), ("selective", None), ("full", 0), ("full", 2)],
)
def test_thread_prompts_and_chat_use_child_model_format(
    tmp_path, monkeypatch, dialect, context_mode, context_window
):
    chat_module = import_module("gptme.chat")
    import gptme.executor
    import gptme.llm.models
    import gptme.prompts

    meta = ModelMeta("mock", "child", context=4096, default_tool_format=dialect)
    monkeypatch.setattr(gptme.llm.models, "set_default_model", lambda model: None)
    monkeypatch.setattr(gptme.llm.models, "get_model", lambda model: meta)
    monkeypatch.setattr(gptme.llm.models, "get_default_model", lambda: meta)
    monkeypatch.setattr(
        gptme.executor, "prepare_execution_environment", lambda **kw: None
    )
    monkeypatch.setattr(execution, "_ensure_subagent_signal_tools_loaded", lambda: None)
    monkeypatch.setattr(execution, "get_tools", lambda: [])
    monkeypatch.setattr(execution, "_load_agent_memory", lambda profile: ("", None))
    prompt = MagicMock(return_value=[])
    monkeypatch.setattr(gptme.prompts, "get_prompt", prompt)
    monkeypatch.setattr(gptme.prompts, "prompt_gptme", lambda *args, **kwargs: [])
    tool_prompt = MagicMock(return_value=[])
    monkeypatch.setattr(gptme.prompts, "prompt_tools", tool_prompt)
    chat = MagicMock()
    monkeypatch.setattr(chat_module, "chat", chat)

    execution._create_subagent_thread(
        "task",
        tmp_path / "log",
        "mock/child",
        context_mode,
        ["tools"],
        tmp_path,
        context_window=context_window,
    )

    selected_prompt = (
        tool_prompt if context_mode == "selective" or context_window == 0 else prompt
    )
    assert selected_prompt.call_args.kwargs["tool_format"] == dialect
    assert chat.call_args.kwargs["tool_format"] == dialect


@pytest.mark.parametrize("dialect", ["markdown", "xml", "tool"])
def test_subprocess_passes_child_format_over_parent_env(tmp_path, monkeypatch, dialect):
    import gptme.llm.models

    meta = ModelMeta("mock", "child", context=4096, default_tool_format=dialect)
    monkeypatch.setattr(gptme.llm.models, "get_model", lambda model: meta)
    monkeypatch.setenv("GPTME_TOOL_FORMAT", "markdown")
    monkeypatch.setattr(execution, "_load_agent_memory", lambda profile: ("", None))
    popen = MagicMock()
    monkeypatch.setattr(execution.subprocess, "Popen", popen)
    logdir = tmp_path / "subagent-test"
    logdir.mkdir()

    execution._run_subagent_subprocess("task", logdir, "mock/child", tmp_path)

    command = popen.call_args.args[0]
    assert command[command.index("--tool-format") + 1] == dialect


@pytest.mark.parametrize("has_default", [False, True])
def test_child_format_falls_back_to_markdown(tmp_path, monkeypatch, has_default):
    import gptme.llm.models
    from gptme.tools.base import get_tool_format, set_tool_format

    meta = ModelMeta("mock", "child", context=4096) if has_default else None
    monkeypatch.setattr(gptme.llm.models, "get_default_model", lambda: meta)
    old = get_tool_format()
    try:
        set_tool_format("tool")
        assert execution._child_tool_format(None) == "markdown"
        assert get_tool_format() == "tool"
    finally:
        set_tool_format(old)
