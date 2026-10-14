"""Strict, opt-in selector request bounds; no live provider requests."""

from typing import Any
from unittest.mock import Mock

import pytest

from gptme import llm
from gptme.hooks import HookType, clear_hooks, register_hook
from gptme.message import Message


@pytest.fixture(autouse=True)
def isolated_hooks():
    clear_hooks()
    yield
    clear_hooks()


def test_input_limit_checks_utf8_after_generation_hooks(monkeypatch):
    send = Mock(return_value=Message("assistant", "ok"))
    monkeypatch.setattr(llm, "_reply_after_hooks", send)

    def extra(*args, **kwargs):
        yield Message("system", "🚀")

    register_hook("extra", HookType.GENERATION_PRE, extra)
    with pytest.raises(ValueError, match="input byte"):
        llm.reply(
            [Message("user", "a")],
            "anthropic/claude-haiku-4-5",
            max_tokens=32,
            max_input_bytes=4,
            max_provider_requests=1,
        )
    send.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_input_bytes": 100},
        {"max_input_bytes": True, "max_provider_requests": 1},
        {"max_input_bytes": 100, "max_provider_requests": 0},
        {"max_input_bytes": 100, "max_provider_requests": 1, "max_tokens": None},
        {"max_input_bytes": 100, "max_provider_requests": 1, "stream": True},
    ],
)
def test_invalid_strict_bounds_fail_before_provider(monkeypatch, kwargs):
    send = Mock()
    monkeypatch.setattr(llm, "_reply_after_hooks", send)
    with pytest.raises(ValueError, match="strict request bounds"):
        llm.reply(
            [Message("user", "a")],
            "anthropic/claude-haiku-4-5",
            **{"max_tokens": 32, **kwargs},
        )
    send.assert_not_called()


@pytest.fixture(params=["anthropic", "openrouter"])
def provider_client(request, monkeypatch):
    """Exercise real SDK requests with in-process HTTP transports only."""
    import httpx
    import httpx2
    from anthropic import Anthropic
    from openai import OpenAI

    from gptme.llm import llm_anthropic, llm_openai

    sent = []
    status = {"code": 503}
    provider = request.param
    transport_module = httpx2 if provider == "anthropic" else httpx

    def handle(req):
        sent.append(req)
        if status["code"] == 200:
            body: dict[str, Any] = (
                {
                    "id": "msg_offline",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5",
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }
                if provider == "anthropic"
                else {
                    "id": "chatcmpl_offline",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "anthropic/claude-haiku-4.5",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "total_tokens": 11,
                    },
                }
            )
            return transport_module.Response(200, json=body)
        return transport_module.Response(
            status["code"],
            json={
                "error": {
                    "message": "No endpoints found"
                    if status["code"] == 404
                    else "unavailable",
                    "type": "api_error",
                }
            },
        )

    http_client = transport_module.Client(
        transport=transport_module.MockTransport(handle)
    )
    client: Anthropic | OpenAI
    if provider == "anthropic":
        client = Anthropic(
            api_key="offline",
            max_retries=0,
            http_client=http_client,
            base_url="https://api.anthropic.com",
        )
        monkeypatch.setattr(llm_anthropic, "_anthropic", client)
        monkeypatch.setattr(llm_anthropic, "_is_proxy", False)
        monkeypatch.setattr(llm_anthropic, "backoff_wait", lambda *args: False)
        model = "anthropic/claude-haiku-4-5"
    else:
        client = OpenAI(
            api_key="offline",
            max_retries=0,
            http_client=http_client,
            base_url="https://openrouter.ai/api/v1",
        )
        monkeypatch.setitem(llm_openai.clients, "openrouter", client)
        monkeypatch.setattr(llm_openai, "backoff_wait", lambda *args: False)
        model = "openrouter/anthropic/claude-haiku-4.5"
    monkeypatch.setattr(llm, "init_llm", lambda provider: None)
    yield client, model, sent, status
    client.close()


def strict_reply(model, **kwargs):
    return llm.reply(
        [Message("system", ""), Message("user", "select this")],
        model,
        **{
            "max_tokens": 64,
            "max_input_bytes": 4096,
            "max_provider_requests": 1,
            **kwargs,
        },
    )


def test_sdk_failures_consume_one_request_and_scope_resets(provider_client):
    import json

    from gptme.llm.request_limits import bounded_attempts

    client, model, sent, _ = provider_client
    for count in (1, 2):
        with pytest.raises(Exception, match="unavailable"):
            strict_reply(model)
        assert len(sent) == count
        assert json.loads(sent[-1].content)["max_tokens"] == 64
        assert bounded_attempts(11) == 11


def test_serialized_input_overhead_is_bounded_before_http(provider_client):
    _, model, sent, _ = provider_client
    with pytest.raises(ValueError, match="serialized input byte"):
        strict_reply(model, max_input_bytes=len("select this"))
    assert not sent


@pytest.mark.parametrize("unsafe", ["sdk-retries", "proxy"])
def test_unsupported_sdk_configuration_is_rejected(provider_client, unsafe):
    client, model, sent, _ = provider_client
    if unsafe == "sdk-retries":
        client.max_retries = 2
    else:
        client.base_url = "https://proxy.invalid/v1/"
    with pytest.raises(ValueError, match="SDK retries|unsupported endpoint"):
        strict_reply(model)
    assert not sent


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_openrouter_inline_fallback_cannot_weaken_parameter_enforcement(
    provider_client,
):
    _, model, sent, status = provider_client
    status["code"] = 404
    with pytest.raises(ValueError, match="parameter support"):
        strict_reply(model, max_provider_requests=2)
    assert len(sent) == 1


def test_two_request_allowance_bounds_retries(provider_client):
    _, model, sent, _ = provider_client
    with pytest.raises(Exception, match="unavailable"):
        strict_reply(model, max_provider_requests=2)
    assert len(sent) == 2


@pytest.mark.parametrize(
    "model",
    [
        "openai/gpt-4o",
        "gptme/anthropic/claude-haiku-4-5",
        "openai-subscription/gpt-6",
        "bare",
    ],
)
def test_unsupported_provider_fails_before_initialization(monkeypatch, model):
    init = Mock()
    monkeypatch.setattr(llm, "init_llm", init)
    with pytest.raises(ValueError, match="supported API provider"):
        strict_reply(model)
    init.assert_not_called()


def test_attachment_added_by_hook_fails_before_initialization(monkeypatch, tmp_path):
    init = Mock()
    monkeypatch.setattr(llm, "init_llm", init)

    def attachment(*args, **kwargs):
        yield Message("user", "image", files=[tmp_path / "image.png"])

    register_hook("attachment", HookType.GENERATION_PRE, attachment)
    with pytest.raises(ValueError, match="attachments"):
        strict_reply("anthropic/claude-haiku-4-5")
    init.assert_not_called()


def test_context_selector_threads_strict_bounds():
    from types import SimpleNamespace
    from unittest.mock import patch

    from gptme.context.selector import ContextSelectorConfig, LLMSelector

    config = ContextSelectorConfig(
        llm_model="anthropic/claude-haiku-4-5",
        max_tokens=64,
        max_input_bytes=4096,
        max_provider_requests=1,
    )
    with patch.object(
        llm, "reply", return_value=Message("assistant", "<selected>x</selected>")
    ) as send:
        LLMSelector(config).select(
            "query", [SimpleNamespace(identifier="x", content="desc", metadata={})]
        )
    assert send.call_args.kwargs["max_input_bytes"] == 4096
    assert send.call_args.kwargs["max_provider_requests"] == 1
    assert send.call_args.kwargs["max_tokens"] == 64


def test_normal_calls_keep_existing_retry_policy(provider_client, monkeypatch):
    _, model, sent, _ = provider_client
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setenv("GPTME_LLM_MAX_RETRIES", "3")
    with pytest.raises(Exception, match="unavailable"):
        strict_reply(model, max_input_bytes=None, max_provider_requests=None)
    assert len(sent) == 3


def test_successful_strict_calls_reset_allowance(provider_client):
    _, model, sent, status = provider_client
    status["code"] = 200
    assert strict_reply(model).content == "ok"
    assert strict_reply(model).content == "ok"
    assert len(sent) == 2
