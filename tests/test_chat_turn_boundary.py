"""Offline regressions for the CLI turn return and post-hook boundary."""

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest

from gptme.chat import StepPersister
from gptme.constants import DECLINED_CONTENT
from gptme.hooks import HookType
from gptme.logmanager import LogManager
from gptme.message import Message
from gptme.tools.complete import SessionCompleteException

chat = importlib.import_module("gptme.chat")


@pytest.mark.parametrize("outcome", ["normal", "declined", "interrupt", "budget"])
def test_turn_result_and_post_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    manager = LogManager([Message("user", "work")], logdir=tmp_path, lock=False)
    post_calls: list[HookType] = []
    completion = SessionCompleteException("policy block budget exhausted: 6 blocks")

    def hooks(hook_type: HookType, **kwargs: object) -> list[Message]:
        if hook_type == HookType.STEP_PRE and outcome == "budget":
            raise completion
        if hook_type == HookType.TURN_POST:
            post_calls.append(hook_type)
            return [Message("system", "post-turn output")]
        return []

    def step(*args: object, **kwargs: object) -> list[Message]:
        if outcome == "interrupt":
            raise KeyboardInterrupt
        return [
            Message("system", DECLINED_CONTENT)
            if outcome == "declined"
            else Message("assistant", "done")
        ]

    monkeypatch.setattr(chat, "trigger_hook", hooks)
    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_args: False)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)

    if outcome == "budget":
        with pytest.raises(SessionCompleteException) as raised:
            chat._process_message_conversation(manager, False, "markdown", None)
        assert raised.value is completion
    else:
        result = chat._process_message_conversation(manager, False, "markdown", None)
        assert result is (outcome in ("declined", "interrupt"))

    assert post_calls == [HookType.TURN_POST]
    assert manager.log[-1].content == "post-turn output"
    reloaded = LogManager.load(tmp_path, lock=False)
    assert reloaded.log[-1].content == "post-turn output"


def test_turn_post_failure_preserves_session_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing TURN_POST hook must not replace the budget-stop signal."""
    manager = LogManager([Message("user", "work")], logdir=tmp_path, lock=False)
    completion = SessionCompleteException("policy block budget exhausted: 6 blocks")
    post_error = RuntimeError("post-turn hook failed")

    def hooks(hook_type: HookType, **kwargs: object) -> list[Message]:
        if hook_type == HookType.STEP_PRE:
            raise completion
        if hook_type == HookType.TURN_POST:
            raise post_error
        return []

    monkeypatch.setattr(chat, "trigger_hook", hooks)
    monkeypatch.setattr(chat, "step", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_args: False)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)

    with pytest.raises(SessionCompleteException) as raised:
        chat._process_message_conversation(manager, False, "markdown", None)
    assert raised.value is completion


@pytest.mark.parametrize("interrupt", [False, True])
def test_reply_persisted_before_tools_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: bool
) -> None:
    """#4956: the CLI loop (which thread-mode subagents run) must write the
    reply before its tools run, so a live reader sees the in-flight tool call
    and an interrupt mid-tool keeps the step record."""
    from gptme.constants import INTERRUPT_CONTENT

    manager = LogManager([Message("user", "work")], logdir=tmp_path, lock=False)
    reply = Message("assistant", "```shell\nsleep 300\n```", quiet=True)
    seen_during_tool: list[list[str]] = []

    def step(*args: object, **kwargs: object):
        on_before = kwargs["on_before_tool_execution"]
        assert callable(on_before)
        on_before(reply)
        seen_during_tool.append(
            [m.role for m in LogManager.load(tmp_path, lock=False).log]
        )
        if interrupt:
            raise KeyboardInterrupt
        yield reply.replace(metadata={"timings": {"tool_ms": 7.0}})
        yield Message("system", "Ran command", quiet=True)

    monkeypatch.setattr(chat, "trigger_hook", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(chat, "step", step)
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_args: False)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")

    chat._process_message_conversation(manager, False, "markdown", None)

    assert seen_during_tool == [["user", "assistant"]]
    reloaded = LogManager.load(tmp_path, lock=False).log
    if interrupt:
        assert [m.content for m in reloaded] == [
            "work",
            reply.content,
            INTERRUPT_CONTENT,
        ]
    else:
        # The completed copy was skipped; the original reply stayed unchanged.
        assert [m.content for m in reloaded][:3] == [
            "work",
            reply.content,
            "Ran command",
        ]
        assert [m.role for m in reloaded].count("assistant") == 1
        assert reloaded[1].to_dict() == reply.to_dict()


def test_resume_closes_tool_call_left_by_hard_kill(tmp_path: Path) -> None:
    """A reply persisted before its tool ran must not reach the provider unanswered."""
    call = '@shell(call_1): {"command": "sleep 99"}'
    manager = LogManager(
        [
            Message("user", "work"),
            Message("assistant", call, metadata={"tool_format": "tool"}),
        ],
        logdir=tmp_path,
        lock=False,
    )

    assert chat._close_interrupted_tool_calls(manager) is True

    result, marker = manager.log[-2], manager.log[-1]
    assert (result.role, result.call_id) == ("system", "call_1")
    assert marker.content == chat.INTERRUPT_CONTENT
    assert chat._should_prompt_for_input(manager.log)
    # Idempotent, and a completed call is left alone.
    assert chat._close_interrupted_tool_calls(manager) is False
    reloaded = LogManager.load(tmp_path, lock=False)
    assert reloaded.log[-1].content == chat.INTERRUPT_CONTENT


def test_resume_leaves_answered_tool_call_alone(tmp_path: Path) -> None:
    call = '@shell(call_1): {"command": "ls"}'
    manager = LogManager(
        [
            Message("user", "work"),
            Message("assistant", call, metadata={"tool_format": "tool"}),
            Message("system", "ok", call_id="call_1"),
        ],
        logdir=tmp_path,
        lock=False,
    )
    assert chat._close_interrupted_tool_calls(manager) is False
    assert len(manager.log) == 3


@pytest.mark.parametrize("resume_role", ["user", "system"])
def test_chat_repairs_interrupted_call_before_resume_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_role: Literal["user", "system"],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resume-only instructions must not mask a saved, unanswered native call."""
    logdir = tmp_path / "conversation"
    manager = LogManager(
        [
            Message("user", "work"),
            Message(
                "assistant",
                '@shell(call_1): {"command": "sleep 99"}',
                metadata={"tool_format": "tool"},
            ),
        ],
        logdir=logdir,
        lock=False,
    )
    manager.write()
    resume = Message(
        resume_role, "resume instructions", metadata={"resume_key": "test"}
    )
    seen: list[Message] = []

    def run_loop(manager: LogManager, *args: object, **kwargs: object) -> None:
        seen.extend(manager.log)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chat, "init", lambda *args: None)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_run_chat_loop", run_loop)
    capsys.readouterr()
    chat.chat(
        [],
        [],
        logdir,
        tmp_path,
        model="openai/gpt-4o-mini",
        stream=False,
        interactive=False,
        tool_format="tool",
        resume_msgs=[resume],
    )

    # Recovery is displayed by history replay, not once on append and again
    # on replay. quiet=True still preserves visibility when printing history.
    assert capsys.readouterr().out.count("Tool call interrupted:") == 1
    assert [m.call_id for m in seen if m.call_id] == ["call_1"]
    assert seen[2].call_id == "call_1"
    assert seen[3].content == chat.INTERRUPT_CONTENT
    assert seen[4].content == resume.content
    assert LogManager.load(logdir, lock=False).log.messages == seen


def test_native_tool_interrupt_pairs_calls_in_same_cli_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupt inside a tool is handled by execute_msg, not the CLI catch."""
    from gptme.tools import ToolUse, init_tools
    from gptme.tools.base import set_tool_format

    init_tools(allowlist=["shell"])
    set_tool_format("tool")
    manager = LogManager([Message("user", "work")], logdir=tmp_path, lock=False)
    reply = Message(
        "assistant",
        '@shell(call_1): {"command": "sleep 99"}\n@shell(call_2): {"command": "pwd"}',
        metadata={"tool_format": "tool"},
    )
    executed: list[str | None] = []

    def interrupted_tool(self: ToolUse, **kwargs: object) -> Iterator[Message]:
        executed.append(self.call_id)
        raise KeyboardInterrupt
        yield  # Keep the real execute_msg generator/interrupt boundary.

    monkeypatch.setattr(ToolUse, "execute", interrupted_tool)
    monkeypatch.setattr(chat, "reply", lambda *a, **kw: reply)
    monkeypatch.setattr(chat, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(chat, "_run_post_tool_compaction", lambda *_args: False)
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.setenv("GPTME_MAX_STEPS", "1")

    assert chat._process_message_conversation(
        manager, False, "tool", "openai/gpt-4o-mini"
    )
    assert executed == ["call_1"]
    assert [m.call_id for m in manager.log if m.call_id] == ["call_1", "call_2"]
    assert manager.log[2].content == chat.INTERRUPT_CONTENT
    manager.append(Message("user", "continue"))
    reloaded = LogManager.load(tmp_path, lock=False)
    assert [m.call_id for m in reloaded.log if m.call_id] == ["call_1", "call_2"]


@pytest.mark.parametrize("answered", [False, True])
def test_cli_cancel_before_executor_pairs_native_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answered: bool
) -> None:
    """A signal after early persistence must be repaired before the next prompt."""
    from gptme.tools import init_tools
    from gptme.tools.base import set_tool_format

    init_tools(allowlist=["shell"])
    set_tool_format("tool")
    manager = LogManager([Message("user", "work")], logdir=tmp_path, lock=False)
    reply = Message(
        "assistant",
        '@shell(call_1): {"command": "pwd"}\n@shell(call_2): {"command": "pwd"}',
        metadata={"tool_format": "tool"},
    )
    before_tools = chat.StepPersister.before_tools

    def cancel_after_persisting(self: StepPersister, msg: Message) -> None:
        before_tools(self, msg)
        if answered:
            for call_id in ("call_1", "call_2"):
                manager.append(Message("system", "completed", call_id=call_id))
        raise KeyboardInterrupt

    monkeypatch.setattr(chat.StepPersister, "before_tools", cancel_after_persisting)
    monkeypatch.setattr(chat, "reply", lambda *a, **kw: reply)
    monkeypatch.setattr(chat, "trigger_hook", lambda *a, **kw: [])
    monkeypatch.setattr(chat, "get_default_model", lambda: None)
    monkeypatch.delenv("GPTME_MAX_STEPS", raising=False)

    assert chat._process_message_conversation(
        manager, False, "tool", "openai/gpt-4o-mini"
    )
    assert [m.call_id for m in manager.log if m.call_id] == ["call_1", "call_2"]
    assert sum(m.content == chat.INTERRUPT_CONTENT for m in manager.log) == 1
    manager.append(Message("user", "continue"))
    reloaded = LogManager.load(tmp_path, lock=False)
    assert [m.call_id for m in reloaded.log if m.call_id] == ["call_1", "call_2"]
    assert reloaded.log[-1].content == "continue"
