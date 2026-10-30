"""Offline chat integration: retry generations, never replay committed tool steps."""

import importlib
from unittest.mock import Mock

import httpx
import pytest
import requests

from gptme import llm
from gptme.llm import recovery
from gptme.message import Message
from gptme.tools import init_tools


@pytest.fixture
def offline(monkeypatch):
    init_tools(allowlist=["shell"])
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
    return chat


def test_prior_tool_result_retained_new_tool_runs_once(offline, monkeypatch):
    chat = offline
    prior = [
        Message("user", "continue the task"),
        Message("assistant", "```shell\necho already-done\n```\n"),
        Message("system", "already-done result"),
    ]
    calls = []
    executions = []

    def stream(messages, model, tools, **kwargs):
        calls.append(list(messages))
        index = len(calls)

        def gen():
            if index == 1:
                yield "```shell\necho failed-attempt\n```\n\n"
                raise requests.exceptions.ChunkedEncodingError(
                    "fixture cut during drain"
                )
            yield "```shell\necho new-action\n```\n\n"
            return {"model": model}

        return llm._StreamWithMetadata(gen(), model)

    def execute(message, **kwargs):
        executions.append(message.content)
        yield Message("system", "new-action result")

    monkeypatch.setattr(llm, "_stream", stream)
    monkeypatch.setattr(chat, "execute_msg", execute)
    result = list(chat.step(prior, stream=True, model="openai/gpt-4o"))
    assert len(calls) == 2
    assert calls == [prior, prior]
    assert len(executions) == 1
    assert "new-action" in executions[0]
    assert "failed-attempt" not in executions[0]
    assert len(prior) == 3 and prior[-1].content == "already-done result"
    assert all("failed-attempt" not in message.content for message in result)
    assert any(message.content == "new-action result" for message in result)


def test_exhausted_generation_executes_no_tools(offline, monkeypatch):
    chat = offline
    calls = []
    executions = []

    def stream(messages, model, tools, **kwargs):
        calls.append(1)

        def gen():
            yield "```shell\necho do-not-run\n```\n\n"
            raise httpx.RemoteProtocolError("fixture truncated")

        return llm._StreamWithMetadata(gen(), model)

    monkeypatch.setattr(llm, "_stream", stream)
    monkeypatch.setattr(chat, "execute_msg", lambda *a, **kw: executions.append(a))
    with pytest.raises(httpx.RemoteProtocolError):
        list(chat.step([Message("user", "hello")], stream=True, model="openai/gpt-4o"))
    assert len(calls) == 3
    assert executions == []


def test_overflow_and_transient_calls_share_scope(offline, monkeypatch):
    chat = offline
    observed = []

    def recover(manager, messages, model, generate, prepare):
        first = recovery.get_recovery_budget()
        observed.append(first)
        generate(messages)
        observed.append(recovery.get_recovery_budget())
        return generate(messages)

    def reply(messages, *a, **kw):
        recovery.reserve_recovery_request()
        return Message("assistant", "success")

    monkeypatch.setattr("gptme.tools.autocompact.recovery.recover_reply", recover)
    monkeypatch.setattr(chat, "reply", reply)
    messages = [Message("user", "hello")]
    from gptme.logmanager import Log

    result = chat._reply_with_overflow_recovery(
        log=Log(messages),
        msgs=messages,
        model="openai/gpt-4o",
        stream=False,
        tools=None,
        workspace=None,
        output_schema=None,
        on_token=None,
        on_thinking=None,
        logdir=None,
    )
    assert result.content == "success"
    assert observed[0] is observed[1] and observed[0] is not None
    assert observed[0].requests == 2
    assert recovery.get_recovery_budget() is None


@pytest.mark.parametrize("interactive", [True, False])
def test_overflow_budget_exhaustion_returns_to_interactive_prompt(
    offline, monkeypatch, tmp_path, interactive
):
    """A smaller overflow view cannot send again or crash interactive chat."""
    from gptme.logmanager import Log

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path

    def append(message):
        manager.log = manager.log.append(message)

    manager.append.side_effect = append
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 1)
    sends = []

    def complete(*args, **kwargs):
        recovery.reserve_recovery_request()
        sends.append(1)
        response = httpx.Response(
            400, request=httpx.Request("POST", "https://offline.invalid")
        )
        raise httpx.HTTPStatusError(
            "context length exceeded", request=response.request, response=response
        )

    monkeypatch.setattr(llm, "_chat_complete", complete)

    def process(*args):
        # Equivalent to the overflow policy's second, smaller request view:
        # both calls share the budget, and only the first can physically send.
        with recovery.recovery_scope():
            with pytest.raises(httpx.HTTPStatusError):
                llm.reply([Message("user", "large request")], "openai/gpt-4o")
            llm.reply([Message("user", "smaller request")], "openai/gpt-4o")

    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "include_paths", lambda message, *args: message)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    prompt = Mock(return_value=None)
    monkeypatch.setattr(chat, "_get_user_input", prompt)

    def run():
        chat._run_chat_loop(
            manager=manager,
            prompt_queue=[Message("user", "hello")],
            stream=False,
            tool_format="markdown",
            model="openai/gpt-4o",
            interactive=interactive,
        )

    if interactive:
        run()
        prompt.assert_called_once()
        assert any("LLM request failed" in message.content for message in manager.log)
    else:
        with pytest.raises(recovery.RecoveryBudgetExceeded):
            run()
        prompt.assert_not_called()
    assert sends == [1]
    assert not recovery.recovery_active()


def test_real_overflow_view_restored_when_shared_send_budget_exhausted(
    offline, monkeypatch, tmp_path
):
    from gptme.logmanager import LogManager

    chat = offline
    manager = LogManager(
        [
            Message("system", "system prompt"),
            Message("user", "task"),
            Message("system", "large tool result " * 1000),
        ],
        logdir=tmp_path / "conversation",
    )
    original = list(manager.log.messages)
    sends = []
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 1)
    monkeypatch.setattr(
        "gptme.tools.autocompact.recovery.compact_for_overflow",
        lambda active_manager: active_manager.log.messages[:2],
    )

    def complete(*args, **kwargs):
        recovery.reserve_recovery_request()
        sends.append(1)
        response = httpx.Response(
            400, request=httpx.Request("POST", "https://offline.invalid")
        )
        raise httpx.HTTPStatusError(
            "maximum context length exceeded",
            request=response.request,
            response=response,
        )

    monkeypatch.setattr(llm, "_chat_complete", complete)
    execute = Mock()
    monkeypatch.setattr(chat, "execute_msg", execute)
    with pytest.raises(recovery.RecoveryBudgetExceeded) as caught:
        list(
            chat.step(
                manager.log,
                stream=False,
                model="openai/gpt-4o",
                logdir=manager.logdir,
            )
        )
    assert llm.is_llm_reply_error(caught.value)
    assert sends == [1]
    assert manager.current_view is None
    assert manager.log.messages == original
    execute.assert_not_called()
