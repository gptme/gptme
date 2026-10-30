"""Offline generation replay: only complete attempts may escape reply()."""

import asyncio
import copy
from unittest.mock import Mock

import httpx
import pytest
import requests

from gptme import llm
from gptme.hooks import HookType, clear_hooks, register_hook
from gptme.llm import recovery
from gptme.message import Message

MODEL = "openai/gpt-4o"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    clear_hooks()
    monkeypatch.setattr(llm, "init_llm", lambda *a: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *a: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.setattr(recovery, "backoff_wait", lambda *a: False)
    yield
    clear_hooks()


def install_stream(monkeypatch, attempts):
    calls: list[list[Message]] = []
    closed: list[int] = []

    def stream(messages, model, tools, **kwargs):
        index = len(calls)
        calls.append(copy.deepcopy(messages))

        def gen():
            try:
                for value in attempts[index]:
                    if isinstance(value, BaseException):
                        raise value
                    yield value
                return {"model": model, "usage": {"output_tokens": index + 1}}
            finally:
                closed.append(index)

        return llm._StreamWithMetadata(gen(), model)

    monkeypatch.setattr(llm, "_stream", stream)
    return calls, closed


def test_partial_text_is_discarded_and_preview_reset(monkeypatch):
    calls, closed = install_stream(
        monkeypatch, [["discard me\n", requests.ConnectionError("cut")], ["success"]]
    )
    messages = [Message("user", "hello"), Message("assistant", "already committed")]
    before = copy.deepcopy(messages)
    preview: list[str] = []
    notices: list[recovery.RetryNotice] = []

    def reset(notice):
        notices.append(notice)
        preview.clear()

    with recovery.recovery_notifications(reset):
        result = llm.reply(messages, MODEL, stream=True, on_token=preview.append)
    assert result.content == "success"
    assert preview == ["success"]
    assert len(notices) == 1 and notices[0].partial_output
    assert notices[0].attempt == 1 and notices[0].max_attempts == 3
    assert messages == before and calls == [before, before]
    assert closed == [0, 1]
    assert result.metadata is not None
    assert result.metadata["usage"]["output_tokens"] == 2
    assert not recovery.recovery_active()


def test_native_tool_break_must_drain_before_return(monkeypatch):
    # Real native parsing/detection; execute callbacks are never invoked by reply.
    from gptme.tools import init_tools
    from gptme.tools.base import set_tool_format

    set_tool_format("tool")
    tools = init_tools(allowlist=["shell"])
    calls, closed = install_stream(
        monkeypatch,
        [
            [
                '@shell(call_bad): {"command": "unsafe"}',
                httpx.RemoteProtocolError("drain failed"),
            ],
            ['@shell(call_good): {"command": "safe"}'],
        ],
    )
    result = llm.reply([Message("user", "tool")], MODEL, stream=True, tools=tools)
    assert "safe" in result.content and "unsafe" not in result.content
    assert [use.call_id for use in llm.ToolUse.iter_from_content(result.content)] == [
        "call_good"
    ]
    assert len(calls) == 2 and closed == [0, 1]


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bug"),
        requests.HTTPError(response=Mock(status_code=401)),
        requests.HTTPError(response=Mock(status_code=403)),
        requests.HTTPError(response=Mock(status_code=400)),
    ],
)
def test_permanent_errors_do_not_retry(monkeypatch, error):
    calls, _ = install_stream(monkeypatch, [[error]])
    with pytest.raises(type(error)) as caught:
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert caught.value is error and len(calls) == 1


@pytest.mark.parametrize("cancel", [KeyboardInterrupt(), asyncio.CancelledError()])
def test_cancellation_propagates_without_partial_message(monkeypatch, cancel):
    calls, closed = install_stream(monkeypatch, [["partial", cancel]])
    with pytest.raises(type(cancel)):
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert len(calls) == 1 and closed == [0]
    assert not recovery.recovery_active()


def test_exhaustion_returns_last_error_not_partial_message(monkeypatch):
    errors = [requests.exceptions.ChunkedEncodingError(str(i)) for i in range(3)]
    calls, _ = install_stream(monkeypatch, [["partial\n", e] for e in errors])
    with pytest.raises(requests.exceptions.ChunkedEncodingError) as caught:
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert caught.value is errors[-1] and len(calls) == 3


def test_callback_transport_exception_is_not_provider_failure(monkeypatch):
    calls, _ = install_stream(monkeypatch, [["hello\n"]])
    error = requests.ConnectionError("callback, not provider")

    def callback(_):
        raise error

    with pytest.raises(requests.ConnectionError) as caught:
        llm.reply([Message("user", "hi")], MODEL, stream=True, on_token=callback)
    assert caught.value is error and len(calls) == 1
    assert not llm.is_provider_error(error)


def test_generation_hooks_run_once_and_hook_errors_are_not_retried(monkeypatch):
    calls, _ = install_stream(monkeypatch, [[httpx.ReadTimeout("timeout")], ["ok"]])
    hook = Mock(return_value=iter([Message("system", "context")]))
    register_hook("context", HookType.GENERATION_PRE, hook)
    llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert hook.call_count == 1 and len(calls[0]) == 2


def test_retry_after_cannot_fit_deadline(monkeypatch):
    response = requests.Response()
    response.status_code = 429
    response.headers["Retry-After"] = "1000"
    error = requests.HTTPError(response=response)
    calls, _ = install_stream(monkeypatch, [[error]])
    sleep = Mock(return_value=False)
    monkeypatch.setattr(recovery, "backoff_wait", sleep)
    with pytest.raises(requests.HTTPError):
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert len(calls) == 1
    sleep.assert_not_called()


def test_recovery_notification_error_propagates(monkeypatch):
    calls, _ = install_stream(monkeypatch, [[httpx.ReadError("provider")]])

    def callback(notice):
        raise requests.ConnectionError("reset failed")

    with (
        recovery.recovery_notifications(callback),
        pytest.raises(requests.ConnectionError, match="reset failed"),
    ):
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert len(calls) == 1 and not recovery.recovery_active()


def test_nonstreaming_transport_retry_and_active_budget(monkeypatch):
    budgets = []

    def complete(*args, **kwargs):
        budgets.append(recovery.get_recovery_budget())
        if len(budgets) == 1:
            raise httpx.ReadTimeout("timeout")
        return "ok", {"model": MODEL}

    monkeypatch.setattr(llm, "_chat_complete", complete)
    result = llm.reply([Message("user", "hi")], MODEL)
    assert result.content == "ok"
    assert budgets[0] is not None
    assert budgets[0] is budgets[1] and budgets[0].attempts == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 503])
@pytest.mark.parametrize("sdk", ["openai", "anthropic"])
def test_sdk_status_classification(status, sdk):
    import importlib

    module = importlib.import_module(sdk)
    transport = importlib.import_module("httpx2" if sdk == "anthropic" else "httpx")
    response = transport.Response(
        status, request=transport.Request("POST", "https://offline.invalid")
    )
    error = module.APIStatusError("fixture", response=response, body=None)
    llm.mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error) == (
        status in (408, 429) or status >= 500
    )


@pytest.mark.parametrize(
    ("code", "transient"),
    [
        ("server_error", True),
        ("rate_limit_exceeded", True),
        ("invalid_api_key", False),
        ("invalid_request_error", False),
        ("context_length_exceeded", False),
        ("insufficient_quota", False),
        ("unknown", False),
    ],
)
def test_responses_failed_classification(code, transient):
    from gptme.llm.openai_responses import ResponsesStreamError

    error = ResponsesStreamError("response.failed", code, "fixture")
    llm.mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error) == transient


@pytest.mark.parametrize("transport_name", ["httpx", "httpx2"])
@pytest.mark.parametrize(
    ("error_name", "transient"),
    [
        ("ReadError", True),
        ("ReadTimeout", True),
        ("RemoteProtocolError", True),
        ("LocalProtocolError", False),
        ("UnsupportedProtocol", False),
    ],
)
def test_transport_classification(transport_name, error_name, transient):
    import importlib

    transport = importlib.import_module(transport_name)
    error = getattr(transport, error_name)("fixture")
    llm.mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error) == transient


def test_chunk_hooks_with_external_effects_prevent_replay(monkeypatch):
    calls, _ = install_stream(monkeypatch, [["spoken aloud\n", httpx.ReadError("cut")]])
    spoken = []

    def speak(*args, **kwargs):
        spoken.append(kwargs["chunk"])
        return iter([])

    register_hook("speech", HookType.GENERATION_CHUNK, speak)
    with pytest.raises(httpx.ReadError):
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert len(calls) == 1 and spoken == ["spoken aloud\n"]


def test_backoff_abort_stops_replay(monkeypatch):
    error = httpx.ReadError("cut")
    calls, _ = install_stream(monkeypatch, [[error]])
    monkeypatch.setattr(recovery, "backoff_wait", lambda *args: True)
    with pytest.raises(httpx.ReadError) as caught:
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert caught.value is error and len(calls) == 1


def test_reset_callback_consumes_deadline(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: now[0])
    calls, _ = install_stream(monkeypatch, [[httpx.ReadError("cut")]])
    wait = Mock(return_value=False)
    monkeypatch.setattr(recovery, "backoff_wait", wait)

    def reset(notice):
        now[0] += 601

    with recovery.recovery_notifications(reset), pytest.raises(httpx.ReadError):
        llm.reply([Message("user", "hi")], MODEL, stream=True)
    assert len(calls) == 1
    wait.assert_not_called()


def test_actual_sends_share_budget_even_with_fallback(monkeypatch):
    requests_sent = []

    def generate():
        for _ in range(4):
            recovery.reserve_recovery_request()
            requests_sent.append(1)

    with pytest.raises(recovery.RecoveryBudgetExceeded, match="request budget"):
        recovery.recover_generation(generate)
    assert len(requests_sent) == 3 and not recovery.recovery_active()


def test_send_deadline_and_remaining(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(recovery.time, "monotonic", lambda: now[0])

    def generate():
        assert recovery.recovery_remaining_seconds() == 600
        now[0] += 601
        assert recovery.recovery_remaining_seconds() == 0
        recovery.reserve_recovery_request()

    with pytest.raises(recovery.RecoveryBudgetExceeded, match="deadline"):
        recovery.recover_generation(generate)
    assert recovery.recovery_remaining_seconds() is None
    recovery.reserve_recovery_request()  # no-op outside generation scope


def test_shared_scope_retries_and_does_not_reset_allowance(monkeypatch):
    calls = []

    def generate():
        recovery.reserve_recovery_request()
        calls.append(1)
        if len(calls) == 1:
            error = httpx.ReadError("transient")
            llm.mark_llm_reply_origin(error)
            raise error
        return "ok"

    with recovery.recovery_scope() as budget:
        with recovery.recovery_scope() as nested:
            assert budget is nested
        assert recovery.recover_generation(generate) == "ok"
        assert budget.requests == 2 and budget.attempts == 2
        assert recovery.recover_generation(generate) == "ok"
        assert budget.requests == 3
        with pytest.raises(recovery.RecoveryBudgetExceeded):
            recovery.recover_generation(generate)
        assert len(calls) == 3
    assert not recovery.recovery_active()


@pytest.mark.parametrize(
    ("limit", "expected"), [("1", 1), ("2", 2), ("100", 3), ("0", 3), ("bad", 3)]
)
def test_legacy_test_ceiling_never_increases_policy(monkeypatch, limit, expected):
    monkeypatch.setenv("GPTME_TEST_MAX_RETRIES", limit)
    with recovery.recovery_scope() as budget:
        assert budget.max_attempts == expected
