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


def test_interactive_exhaustion_parks_pending_intent(offline, monkeypatch, tmp_path):
    """Failed turns must not discard queued intent or auto-retry it."""
    from gptme.logmanager import Log
    from gptme.prompt_queue import drain_prompt_queue, get_prompt_queue_path

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message(
        "user",
        "queued follow-through must remain recoverable",
        metadata={"skill_invocation_id": "invocation-123"},
    )
    queue = [Message("user", "initial work"), pending]
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    process = Mock(side_effect=error)
    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_get_user_input", Mock(return_value=None))

    chat._run_chat_loop(
        manager,
        queue,
        stream=False,
        interactive=True,
        logdir=tmp_path,
    )

    assert process.call_count == 1, (
        "exhaustion must return control, not restart a queue loop"
    )
    parked = drain_prompt_queue(tmp_path)
    assert [msg.content for msg in parked] == [pending.content]
    assert parked[0].metadata == {"skill_invocation_id": "invocation-123"}
    assert pending not in queue
    assert all(msg.content != pending.content for msg in manager.log)
    assert not get_prompt_queue_path(tmp_path).exists()
    assert any("LLM request failed" in msg.content for msg in manager.log)


def test_interactive_resume_runs_parked_prompt_once(offline, monkeypatch, tmp_path):
    """Parked intent runs after an explicit next user message, not immediately."""
    from gptme.logmanager import Log

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message("user", "parked follow-through")
    queue = [Message("user", "initial work"), pending]
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    calls: list[str] = []

    def process(*args, **kwargs):
        last = manager.log[-1]
        if not calls:
            calls.append("fail")
            raise error
        calls.append(last.content)
        manager.append(Message("assistant", f"ok:{last.content}"))

    inputs = [Message("user", "resume after failure")]

    def get_input(*args, **kwargs):
        return inputs.pop(0) if inputs else None

    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_get_user_input", get_input)

    chat._run_chat_loop(
        manager,
        queue,
        stream=False,
        interactive=True,
        logdir=tmp_path,
    )

    assert calls == ["fail", "resume after failure", "parked follow-through"]
    assert [msg.content for msg in manager.log if msg.role == "user"] == [
        "initial work",
        "resume after failure",
        "parked follow-through",
    ]


@pytest.mark.parametrize("park_fails", [False, True])
@pytest.mark.parametrize("has_pending", [False, True])
def test_interactive_resume_complete_drains_pending_intent(
    offline, monkeypatch, tmp_path, park_fails, has_pending
):
    """Completion on explicit resume must honor pending work and queue closure."""
    from gptme.logmanager import Log
    from gptme.prompt_queue import get_prompt_queue_path
    from gptme.tools.complete import SessionCompleteException

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message("user", "parked follow-through")
    queue = [Message("user", "initial work")]
    if has_pending:
        queue.append(pending)
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    calls: list[str] = []
    sentinel = tmp_path / "prompt-queue-closed"

    def process(*args, **kwargs):
        assert not sentinel.exists(), "queue must stay open while work is running"
        calls.append(manager.log[-1].content)
        if len(calls) == 1:
            raise error
        raise SessionCompleteException("this prompt is done")

    def fail_park(*args, **kwargs):
        raise OSError("disk full")

    if park_fails:
        monkeypatch.setattr(chat, "requeue_prompts", fail_park)
    inputs = Mock(return_value=Message("user", "explicit resume"))
    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_get_user_input", inputs)

    with pytest.raises(SessionCompleteException):
        chat._run_chat_loop(
            manager, queue, stream=False, interactive=True, logdir=tmp_path
        )

    assert calls == ["initial work", "explicit resume"] + (
        [pending.content] if has_pending else []
    )
    inputs.assert_called_once()
    assert not queue
    assert not get_prompt_queue_path(tmp_path).exists()
    assert sentinel.exists(), "final completion must close the steering queue"


def test_headless_exhaustion_parks_then_exits(offline, monkeypatch, tmp_path):
    """Headless exhaustion stays failed and nonzero, with remaining intent parked."""
    from gptme.logmanager import Log
    from gptme.prompt_queue import drain_prompt_queue

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message("user", "headless follow-through")
    queue = [Message("user", "initial work"), pending]
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    process = Mock(side_effect=error)
    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    prompt = Mock(return_value=None)
    monkeypatch.setattr(chat, "_get_user_input", prompt)

    with pytest.raises(httpx.RemoteProtocolError):
        chat._run_chat_loop(
            manager,
            queue,
            stream=False,
            interactive=False,
            logdir=tmp_path,
        )

    assert process.call_count == 1
    prompt.assert_not_called()
    assert [msg.content for msg in drain_prompt_queue(tmp_path)] == [pending.content]
    assert all("LLM request failed" not in msg.content for msg in manager.log)


@pytest.mark.parametrize("failures", [1, 2])
@pytest.mark.parametrize("has_pending", [True, False])
def test_interactive_command_does_not_drain_parked_prompts(
    offline, monkeypatch, tmp_path, failures, has_pending
):
    """Slash commands after exhaustion must not retry parked work."""
    from gptme.logmanager import Log
    from gptme.prompt_queue import drain_prompt_queue

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message("user", "parked follow-through")
    queue = [Message("user", "initial work")]
    if has_pending:
        queue.append(pending)
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    process = Mock(
        side_effect=[error] * failures + [RuntimeError("unexpected generation")]
    )
    inputs = ["retry"] * (failures - 1) + ["/help", "/model"]

    def prompt():
        if inputs:
            return inputs.pop(0)
        raise EOFError

    def execute(msg, manager):
        if msg.content.startswith("/"):
            manager.append(Message("system", "help text"))
            return True
        return False

    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", execute)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    # Exercise the real input decision; mocking it hid automatic generation
    # after a command appended a newer user message over the failure marker.
    monkeypatch.setattr(chat, "prompt_user", prompt)

    chat._run_chat_loop(
        manager,
        queue,
        stream=False,
        interactive=True,
        logdir=tmp_path,
    )

    assert process.call_count == failures
    assert inputs == []
    assert [msg.content for msg in drain_prompt_queue(tmp_path)] == (
        [pending.content] if has_pending else []
    )


def test_interactive_park_oserror_keeps_in_memory_queue(offline, monkeypatch, tmp_path):
    """A failed durable park must not drop in-memory intent or kill the session."""
    from gptme.logmanager import Log

    chat = offline
    manager = Mock()
    manager.log = Log()
    manager.workspace = tmp_path
    manager.logdir = tmp_path
    manager.append.side_effect = lambda msg: setattr(
        manager, "log", manager.log.append(msg)
    )
    pending = Message("user", "unsaved follow-through")
    queue = [Message("user", "initial work"), pending]
    error = httpx.RemoteProtocolError("synthetic exhausted provider")
    llm.mark_llm_reply_origin(error)
    process = Mock(side_effect=error)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(chat, "requeue_prompts", boom)
    monkeypatch.setattr(chat, "_process_message_conversation", process)
    monkeypatch.setattr(chat, "include_paths", lambda msg, *args: msg)
    monkeypatch.setattr(chat, "execute_cmd", lambda *args: False)
    monkeypatch.setattr(chat, "trigger_hook", lambda *args, **kwargs: [])
    monkeypatch.setattr(chat, "_get_user_input", Mock(return_value=None))

    chat._run_chat_loop(
        manager,
        queue,
        stream=False,
        interactive=True,
        logdir=tmp_path,
    )

    assert process.call_count == 1
    assert pending in queue
    assert any("LLM request failed" in msg.content for msg in manager.log)
