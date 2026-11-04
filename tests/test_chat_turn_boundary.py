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
