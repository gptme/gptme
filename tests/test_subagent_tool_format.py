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


def test_thread_without_model_uses_default_model_format(monkeypatch):
    """A model-less thread child's model IS the thread default model.

    In thread mode ``model=None`` means the child runs on the context's default
    model, so resolving the format from that model's metadata is selecting the
    *child's* dialect — not inheriting the parent's tool format.
    """
    import gptme.llm.models

    meta = ModelMeta("mock", "child", context=4096, default_tool_format="xml")
    monkeypatch.setattr(gptme.llm.models, "get_default_model", lambda: meta)

    assert execution._child_tool_format(None) == "xml"


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


def test_subprocess_defers_to_env_when_model_declares_no_format(tmp_path, monkeypatch):
    """A custom provider without metadata must not be forced to markdown.

    Passing ``--tool-format markdown`` unconditionally would clobber a
    workspace ``TOOL_FORMAT``; the flag must be omitted so the CLI resolves
    the dialect itself. The parent cannot know that resolution, so it must not
    assert one in the prompt either (``None`` → dialect-free instruction).
    """
    import gptme.llm.models

    monkeypatch.setattr(gptme.llm.models, "get_model", lambda model: None)
    monkeypatch.setenv("GPTME_TOOL_FORMAT", "xml")
    monkeypatch.delenv("TOOL_FORMAT", raising=False)
    monkeypatch.setattr(execution, "_load_agent_memory", lambda profile: ("", None))
    popen = MagicMock()
    monkeypatch.setattr(execution.subprocess, "Popen", popen)
    logdir = tmp_path / "subagent-test"
    logdir.mkdir()

    execution._run_subagent_subprocess("task", logdir, "custom/child", tmp_path)

    command = popen.call_args.args[0]
    assert "--tool-format" not in command
    assert execution._effective_child_tool_format("custom/child") is None


@pytest.mark.parametrize(
    ("dialect", "expected", "absent"),
    [
        ("markdown", "```complete", "<complete>"),
        ("xml", "<complete>", "```complete"),
        ("tool", "native tool call", "```complete"),
        (None, "in your active tool format", "```complete"),
    ],
)
def test_completion_instruction_matches_child_dialect(dialect, expected, absent):
    """The complete/clarify examples must be parseable in the child's dialect.

    ``None`` (unknown dialect) must not assert any dialect-specific syntax.
    """
    from gptme.tools.subagent.hooks import _get_complete_instruction

    instruction = _get_complete_instruction(tool_format=dialect)
    assert expected in instruction
    assert absent not in instruction
    if dialect is None:
        assert "<complete>" not in instruction
    if dialect == "tool":
        # Native calls carry no text body, so `progress` cannot deliver an
        # update and must not be advertised.
        assert "progress" not in instruction
