"""Tool durations are saved with completed results, never patched into history."""

import importlib
from unittest.mock import patch

import pytest

from gptme.message import Message
from gptme.tools import ToolUse, execute_msg, init_tools


@pytest.mark.parametrize("native", [False, True])
def test_result_timings_persist_per_call(monkeypatch, tmp_path, native):
    from gptme.logmanager import LogManager, eventlog

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    init_tools(allowlist=["shell"])
    chat = importlib.import_module("gptme.chat")
    content = (
        '@shell(call_1): {"command": "one"}\n@shell(call_2): {"command": "two"}'
        if native
        else "```shell\none\n```\n\n```shell\ntwo\n```"
    )
    manager = LogManager.load("result-timings", create=True, lock=False)
    manager.append(Message("user", "run two tools", quiet=True))
    from gptme.tools.base import set_tool_format

    set_tool_format("tool" if native else "markdown")
    reply = Message(
        "assistant",
        content,
        metadata={
            "timings": {"gen_ms": 50.0},
            "tool_format": "tool" if native else "markdown",
        },
    )
    prefixes = []
    from types import SimpleNamespace

    tools_module = importlib.import_module("gptme.tools")
    clock = iter([1.0, 1.125, 2.0, 2.5])
    monkeypatch.setattr(
        tools_module, "time", SimpleNamespace(monotonic=lambda: next(clock))
    )

    def execute(self, **kwargs):
        prefixes.append(manager.logfile.read_bytes())
        index = kwargs["call_index"]
        yield Message("system", "progress", metadata={"tool_call": index})
        yield Message(
            "system",
            "done",
            call_id=self.call_id,
            metadata={
                "tool_call": index,
                "tool": self.tool,
                "tool_status": "failed",
            },
        )
        yield Message("system", "post hook")

    persister = chat.StepPersister(manager)
    with (
        patch.object(chat, "reply", return_value=reply),
        patch.object(ToolUse, "execute", execute),
    ):
        for message in chat.step(
            manager.log, stream=False, on_before_tool_execution=persister.before_tools
        ):
            persister.append(message)

    final = LogManager.load("result-timings", lock=False)
    assert final.logfile.read_bytes().startswith(prefixes[0])
    assert final.log[1].metadata == reply.metadata
    results = [m for m in final.log if m.content == "done"]
    assert len(results) == 2
    for index, result in enumerate(results):
        assert result.metadata is not None
        assert result.metadata["timings"]["tool_ms"] == [125.0, 500.0][index]
        assert result.metadata["tool_call"] == index
        assert result.metadata["tool_status"] == "failed"
        assert result.call_id == (f"call_{index + 1}" if native else None)
    assert all(
        "timings" not in (m.metadata or {})
        for m in final.log
        if m.content in ("progress", "post hook")
    )
    assert eventlog.recover_messages(final.logdir) == [m.to_dict() for m in final.log]


def test_interrupted_result_has_timing_but_unexecuted_call_does_not():
    init_tools(allowlist=["shell"])
    from gptme.tools.base import set_tool_format

    set_tool_format("tool")

    def execute(self, **kwargs):
        yield Message("system", "partial", metadata={"tool_call": kwargs["call_index"]})
        raise KeyboardInterrupt

    with patch.object(ToolUse, "execute", execute):
        outputs = list(
            execute_msg(
                Message(
                    "assistant",
                    '@shell(call_1): {"command": "one"}\n@shell(call_2): {"command": "two"}',
                )
            )
        )
    interrupted = next(m for m in outputs if m.call_id == "call_1")
    skipped = next(m for m in outputs if m.call_id == "call_2")
    assert interrupted.metadata is not None
    assert interrupted.metadata["timings"]["tool_ms"] >= 0
    assert "timings" not in (skipped.metadata or {})


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("edited", [False, True])
def test_server_saves_timed_result_without_rewriting_reply(
    monkeypatch, tmp_path, native, edited
):
    pytest.importorskip("flask")
    from gptme.config import ChatConfig
    from gptme.logmanager import LogManager, eventlog
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import set_tool_format

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    set_tool_format("tool" if native else "markdown")
    conversation_id = "server-result-timings"
    manager = LogManager.load(conversation_id, create=True, lock=False)
    manager.append(Message("user", "run a tool", quiet=True))
    content = (
        '@shell(call_1): {"command": "echo done"}'
        if native
        else "```shell\necho done\n```"
    )
    reply = Message(
        "assistant", content, quiet=True, metadata={"timings": {"gen_ms": 10.0}}
    )
    manager.append(reply)
    prefix = manager.logfile.read_bytes()
    session = SessionManager.create_session(conversation_id)
    tooluse = next(ToolUse.iter_from_content(content))
    session.pending_tools["tool"] = ToolExecution(
        tool_id="tool", tooluse=tooluse, assistant_msg_timestamp=reply.timestamp
    )
    config = ChatConfig(model="mock/model", workspace=tmp_path)
    with (
        patch("gptme.server.session_step._start_step_thread"),
        patch("gptme.server.session_step._compact_after_tool_results"),
    ):
        from dataclasses import replace

        # Match the edit-confirmation route: execution content changes, but
        # parser provenance and the persisted original call remain intact.
        edited_tooluse = replace(tooluse, content="echo edited") if edited else None
        thread = start_tool_execution(
            conversation_id, session, "tool", edited_tooluse, "mock/model", config
        )
        thread.join(timeout=10)
    assert not thread.is_alive()
    final = LogManager.load(conversation_id, lock=False)
    assert final.logfile.read_bytes().startswith(prefix)
    assert final.log[1].to_dict() == reply.to_dict()
    results = [
        m for m in final.log if "timings" in (m.metadata or {}) and m.role == "system"
    ]
    assert len(results) == 1
    assert results[0].metadata is not None
    assert results[0].metadata["timings"]["tool_ms"] >= 0
    assert results[0].metadata["tool_call"] == 0
    assert results[0].call_id == ("call_1" if native else None)
    assert eventlog.recover_messages(final.logdir) == [m.to_dict() for m in final.log]


def test_server_concurrent_results_keep_independent_timings(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    import threading

    from gptme.config import ChatConfig
    from gptme.logmanager import LogManager, eventlog
    from gptme.server.session_models import SessionManager, ToolExecution
    from gptme.server.session_step import start_tool_execution
    from gptme.tools.base import set_tool_format

    monkeypatch.setenv("GPTME_LOGS_HOME", str(tmp_path))
    set_tool_format("tool")
    init_tools(allowlist=["shell"])
    conversation_id = "concurrent-result-timings"
    manager = LogManager.load(conversation_id, create=True, lock=False)
    reply = Message(
        "assistant",
        '@shell(call_1): {"command": "one"}\n@shell(call_2): {"command": "two"}',
        metadata={"tool_format": "tool"},
        quiet=True,
    )
    manager.append(reply)
    prefix = manager.logfile.read_bytes()
    session = SessionManager.create_session(conversation_id)
    for call in ToolUse.iter_from_content(reply.content):
        assert call.call_id is not None
        session.pending_tools[call.call_id] = ToolExecution(
            tool_id=call.call_id, tooluse=call, assistant_msg_timestamp=reply.timestamp
        )
    barrier = threading.Barrier(2)

    def execute(self, **kwargs):
        barrier.wait(timeout=5)
        yield Message(
            "system",
            "done",
            call_id=self.call_id,
            metadata={"tool_call": kwargs["call_index"]},
        )

    with (
        patch.object(ToolUse, "execute", execute),
        patch("gptme.server.session_step.prepare_execution_environment"),
        patch("gptme.server.session_step._start_step_thread"),
        patch("gptme.server.session_step._compact_after_tool_results"),
    ):
        threads = [
            start_tool_execution(
                conversation_id,
                session,
                f"call_{i}",
                None,
                "mock/model",
                ChatConfig(model="mock/model", workspace=tmp_path),
            )
            for i in (1, 2)
        ]
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
    final = LogManager.load(conversation_id, lock=False)
    assert final.logfile.read_bytes().startswith(prefix)
    assert {m.call_id for m in final.log if m.role == "system"} == {"call_1", "call_2"}
    assert all(
        (m.metadata or {})["timings"]["tool_ms"] >= 0
        for m in final.log
        if m.role == "system"
    )
    assert eventlog.recover_messages(final.logdir) == [m.to_dict() for m in final.log]


@pytest.mark.parametrize("outcome", ["completed", "failed", "denied"])
@pytest.mark.parametrize("native", [False, True])
def test_real_tool_result_not_hook_chatter_gets_duration(monkeypatch, outcome, native):
    from gptme.hooks import HookType
    from gptme.tools import ToolSpec
    from gptme.tools.base import set_tool_format

    def execute(code, args, kwargs):
        if outcome == "failed":
            raise RuntimeError("tool broke")
        yield Message("system", "progress")
        yield Message("system", "done")

    def hook(kind, data):
        if kind == HookType.TOOL_EXECUTE_PRE:
            if outcome == "denied":
                data.deny("policy denied")
            return [Message("system", "pre hook")]
        if kind == HookType.TOOL_EXECUTE_POST:
            return [Message("system", "post hook")]
        return []

    monkeypatch.setattr(
        "gptme.tools.get_tool",
        lambda name: ToolSpec(name="shell", desc="test", execute=execute),
    )
    monkeypatch.setattr("gptme.hooks.trigger_hook", hook)
    set_tool_format("tool" if native else "markdown")
    content = '@shell(call_1): {"command": "test"}' if native else "```shell\ntest\n```"
    outputs = list(execute_msg(Message("assistant", content)))
    timed = [m for m in outputs if "timings" in (m.metadata or {})]
    assert len(timed) == 1
    assert timed[0].call_id == ("call_1" if native else None)
    assert timed[0].metadata is not None
    assert timed[0].metadata["timings"]["tool_ms"] >= 0
    assert timed[0].metadata.get("tool_status", "completed") == outcome
    assert all(
        "timings" not in (m.metadata or {})
        for m in outputs
        if m.content in ("progress", "pre hook", "post hook")
    )
