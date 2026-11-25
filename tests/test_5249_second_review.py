"""Regressions for the second tool follow-through review."""

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gptme.message import Message


def test_unreadable_image_header_returns_error(tmp_path):
    from gptme.tools.convert import ImageConverter, ToolAvailability

    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    with (
        patch.object(Path, "open", side_effect=PermissionError("denied")),
        patch(
            "gptme.tools.convert.get_availability",
            return_value=ToolAvailability(ffmpeg=True, imagemagick=True),
        ),
        patch("gptme.tools.convert.subprocess.run") as run,
    ):
        result = ImageConverter().convert(source, tmp_path / "output.tiff")
    assert not result.success
    assert result.error and "denied" in result.error
    run.assert_not_called()


def test_unreadable_child_config_does_not_hide_result(tmp_path):
    from gptme.tools import init_tools
    from gptme.tools.subagent.types import Subagent

    init_tools(["complete"], include_mcp=False)
    messages = [Message("user", "task"), Message("assistant", "```complete\nDone\n```")]
    (tmp_path / "conversation.jsonl").write_text(
        "".join(json.dumps(m.to_dict()) + "\n" for m in messages)
    )
    (tmp_path / "config.toml").write_text("[chat]\nunknown_future_field = true\n")
    child = Subagent(
        agent_id="old", prompt="task", thread=None, logdir=tmp_path, model=None
    )
    result = child._read_log()
    assert result.status == "success"
    assert result.tool_uses == 1


def test_low_compaction_budget_keeps_tool_capable_checkpoint(monkeypatch):
    import gptme.tools.autocompact.hook as hook
    from gptme.llm.models import get_model

    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "1000")
    model = replace(get_model("openai/gpt-4o-mini"), context=100000)
    manager = MagicMock()
    manager.logdir = "/tmp/5249-low-budget"
    manager.current_branch = "master"
    manager.current_view = None
    manager.log.messages = [
        Message("system", "prompt"),
        Message("user", "word " * 2000),
        Message("assistant", "reply"),
    ]
    hook._last_autocompact_attempt.clear()
    hook._failed_summarize.clear()
    with (
        patch.object(hook, "get_default_model", return_value=model),
        patch.object(hook, "should_auto_compact", return_value="summarize"),
        patch.object(hook, "get_project_config", return_value=None),
        patch.object(hook, "_resume_via_llm", return_value=iter(())) as summarize,
    ):
        output = list(hook.autocompact_hook(manager))
    summarize.assert_not_called()
    assert any(
        isinstance(m, Message)
        and m.metadata
        and "compaction_checkpoint_view" in m.metadata
        for m in output
    )


def test_subagent_allowlist_survives_metadata_roundtrip(tmp_path):
    from gptme.tools.subagent.persistence import _dict_to_subagent, _subagent_to_dict
    from gptme.tools.subagent.types import Subagent

    child = Subagent(
        agent_id="restricted",
        prompt="task",
        thread=None,
        logdir=tmp_path,
        model=None,
        parent_tool_allowlist=["ipython"],
    )
    restarted = _dict_to_subagent(_subagent_to_dict(child))
    assert restarted.parent_tool_allowlist == ["ipython"]


@pytest.mark.parametrize("mode", ["thread", "subprocess"])
def test_continuation_keeps_original_allowlist(tmp_path, monkeypatch, mode):
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import Subagent, _subagents, _subagents_lock

    child = Subagent(
        agent_id="restricted",
        prompt="task",
        thread=None,
        logdir=tmp_path,
        model=None,
        execution_mode=mode,
        parent_tool_allowlist=["ipython"],
    )
    monkeypatch.setattr(Subagent, "continuation_blocker", lambda *a, **kw: None)
    monkeypatch.setattr(api, "get_session_allowlist", lambda: None)
    monkeypatch.setattr(api, "_caller_owner", lambda: None)
    captured = []

    def launch(**kwargs):
        captured.append(kwargs["parent_tool_allowlist"])
        return MagicMock()

    monkeypatch.setattr(api._exec, "_create_subagent_thread", launch)
    monkeypatch.setattr(api._exec, "_run_subagent_subprocess", launch)
    monkeypatch.setattr(api._exec, "_monitor_subprocess", lambda *a: None)
    with _subagents_lock:
        _subagents.append(child)
    try:
        api.subagent_continue("restricted", "continue")
        continued = next(s for s in reversed(_subagents) if s.agent_id == "restricted")
        assert continued.thread is not None
        continued.thread.join(timeout=10)
        assert not continued.thread.is_alive()
        assert captured == [["ipython"]]
        assert continued.parent_tool_allowlist == ["ipython"]
    finally:
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "restricted"]


def test_profiled_subprocess_keeps_approved_file(tmp_path):
    from gptme.tools.subagent.execution import _subprocess_tools_arg

    approved = tmp_path / "approved.py"
    approved.write_text(
        'from gptme.tools.base import ToolSpec\ntool = ToolSpec(name="approved", desc="read", hints=frozenset({"read-only"}))\n'
    )
    result = _subprocess_tools_arg(["hint:read-only"], [str(approved)])
    assert str(approved) in result.split(",")


def test_profiled_subprocess_rejects_partial_file_instead_of_widening(tmp_path):
    from gptme.tools.subagent.execution import _subprocess_tools_arg

    approved = tmp_path / "approved.py"
    approved.write_text(
        'from gptme.tools.base import ToolSpec\nread = ToolSpec(name="approved_read", desc="read")\nwrite = ToolSpec(name="approved_write", desc="write")\n'
    )
    with pytest.raises(ValueError, match="only part of tool file"):
        _subprocess_tools_arg(["approved_read"], [str(approved)])


def test_subprocess_tool_patterns_resolve_in_parent_context(tmp_path, monkeypatch):
    import importlib
    import threading
    from contextvars import ContextVar
    from types import SimpleNamespace

    from gptme.tools.base import ToolSpec
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import _subagents, _subagents_lock

    parent = ContextVar("project_mcp", default=False)
    parent.set(True)
    discovered = []

    def available():
        discovered.append(parent.get())
        return [ToolSpec(name="project.read", desc="read")] if parent.get() else []

    monkeypatch.setattr(api._exec, "get_available_tools", available)
    monkeypatch.setattr(api, "get_session_allowlist", lambda: ["project.*"])
    monkeypatch.setattr(
        importlib.import_module("gptme.profiles"),
        "get_profile",
        lambda name: SimpleNamespace(tools=["project.read"], model=None),
    )
    monkeypatch.setattr(
        importlib.import_module("gptme.cli.main"),
        "get_logdir",
        lambda name: tmp_path / name,
    )
    monkeypatch.setattr(
        importlib.import_module("gptme.llm.models"), "get_default_model", lambda: None
    )
    captured = []

    def launch(**kwargs):
        captured.append((parent.get(), kwargs["tools_arg"]))
        return MagicMock()

    monkeypatch.setattr(api._exec, "_run_subagent_subprocess", launch)
    monkeypatch.setattr(api._exec, "_monitor_subprocess", lambda *a: None)
    try:
        api.subagent("project-mcp", "task", use_subprocess=True, profile="reader")
        child = next(s for s in reversed(_subagents) if s.agent_id == "project-mcp")
        assert isinstance(child.thread, threading.Thread)
        child.thread.join(timeout=10)
        assert captured and captured[0][0] is False
        assert "project.read" in captured[0][1].split(",")
        assert discovered and all(discovered)
    finally:
        with _subagents_lock:
            _subagents[:] = [s for s in _subagents if s.agent_id != "project-mcp"]


def test_checkpoint_fit_ignores_trigger_cap_but_reserves_output(monkeypatch):
    from gptme.llm.models import get_model
    from gptme.tools.autocompact.resume import _checkpoint_fits

    monkeypatch.setenv("GPTME_CONTEXT_BUDGET", "1000")
    model = replace(
        get_model("openai/gpt-4o-mini"), context=1000000, context_budget=0.9
    )
    with patch("gptme.tools.autocompact.resume.len_tokens", return_value=300000):
        assert _checkpoint_fits([], model)
    with patch("gptme.tools.autocompact.resume.len_tokens", return_value=999999):
        assert not _checkpoint_fits([], model)


def test_clarification_respawn_keeps_boundary_and_restores_caller(
    tmp_path, monkeypatch
):
    from gptme.tools import get_session_allowlist, set_session_allowlist
    from gptme.tools.subagent import api
    from gptme.tools.subagent.types import ReturnType, Subagent

    child = Subagent(
        agent_id="restricted-reply",
        prompt="task",
        thread=None,
        logdir=tmp_path,
        model=None,
        parent_tool_allowlist=["ipython"],
    )
    monkeypatch.setattr(api, "_find_subagent", lambda *a, **kw: child)
    monkeypatch.setattr(
        Subagent, "status", lambda self: ReturnType("clarification_needed", "which?")
    )
    seen = []
    monkeypatch.setattr(
        api, "subagent", lambda **kw: seen.append(get_session_allowlist())
    )
    before = get_session_allowlist()
    set_session_allowlist(["shell"])
    try:
        api.subagent_reply("restricted-reply", "this one")
        assert seen == [[]]
        assert get_session_allowlist() == ["shell"]
    finally:
        set_session_allowlist(before)
