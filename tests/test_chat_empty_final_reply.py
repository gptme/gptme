"""An empty reply after the last tool result ends a headless run without `complete`.

gpt-6.1-sol answers "nothing left to do" with an empty message. Without the
`complete` tool, replaying that request repeated the empty answer up to the
recovery budget and then failed the run, although its work was done.
"""

import importlib
from unittest.mock import Mock

import pytest

from gptme import llm
from gptme.llm import EmptyStreamError, recovery
from gptme.message import Message
from gptme.tools import init_tools
from gptme.tools.complete import SessionCompleteException

AFTER_TOOL = [
    Message("user", "write pick.json"),
    Message("assistant", "```shell\necho saved > pick.json\n```\n"),
    Message("system", "Ran command: saved"),
]


@pytest.fixture
def offline(monkeypatch):
    chat = importlib.import_module("gptme.chat")
    monkeypatch.setattr(llm, "init_llm", lambda *a: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *a: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.setattr(chat, "prepare_messages", lambda messages, *a, **kw: messages)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.setattr(recovery, "backoff_wait", lambda *a: False)
    calls: list[int] = []

    def stream(messages, model, tools, **kwargs):
        calls.append(len(messages))

        def gen():
            yield from ()
            return {"model": model}

        return llm._StreamWithMetadata(gen(), model)

    monkeypatch.setattr(llm, "_stream", stream)
    return chat, calls


def _run_step(chat, messages, headless: bool):
    token = chat._headless_run.set(headless)
    try:
        return list(chat.step(list(messages), stream=True, model="openai/gpt-4o"))
    finally:
        chat._headless_run.reset(token)


def test_headless_without_complete_ends_after_one_request(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    with pytest.raises(SessionCompleteException):
        _run_step(chat, AFTER_TOOL, headless=True)

    assert len(calls) == 1


def test_complete_tool_loaded_keeps_replaying(offline):
    chat, calls = offline
    init_tools(allowlist=["shell", "complete"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, AFTER_TOOL, headless=True)

    assert len(calls) > 1


def test_interactive_keeps_replaying(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, AFTER_TOOL, headless=False)

    assert len(calls) > 1


def test_empty_first_reply_is_still_an_error(offline):
    chat, calls = offline
    init_tools(allowlist=["shell"])

    with pytest.raises(EmptyStreamError):
        _run_step(chat, [Message("user", "write pick.json")], headless=True)

    assert len(calls) > 1


def test_flag_is_scoped_to_the_chat_loop():
    chat = importlib.import_module("gptme.chat")
    assert chat._headless_run.get() is False
