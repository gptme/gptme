"""Offline regressions for the CLI turn return and post-hook boundary."""

import importlib
from pathlib import Path

import pytest

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
        # The completed reply replaced the early copy; it was not appended twice.
        assert [m.content for m in reloaded][:3] == [
            "work",
            reply.content,
            "Ran command",
        ]
        assert [m.role for m in reloaded].count("assistant") == 1
        assert (reloaded[1].metadata or {})["timings"]["tool_ms"] == 7.0
