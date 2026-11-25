"""Strict, opt-in selector request bounds; no live provider requests."""

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from gptme import llm
from gptme.hooks import HookType, clear_hooks, register_hook
from gptme.message import Message


@pytest.fixture(autouse=True)
def isolated_hooks(monkeypatch):
    monkeypatch.setattr("gptme.telemetry.init_telemetry", lambda **kwargs: None)
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
    import anthropic
    import httpx
    import httpx2
    import openai
    from anthropic import Anthropic
    from openai import OpenAI

    from gptme.llm import llm_anthropic, llm_openai

    sent = []
    status = {"code": 503}
    provider = request.param
    transport_module = httpx2 if provider == "anthropic" else httpx

    def handle(req):
        sent.append(req)
        if status["code"] in (307, 308):
            code = status["code"]
            status["code"] = 200
            return transport_module.Response(
                code, headers={"location": "https://unapproved.invalid/replay"}
            )
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
            if provider == "openrouter" and status.get("tool_call"):
                body["choices"][0]["message"] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_offline",
                            "type": "function",
                            "function": {
                                "name": "shell",
                                "arguments": '{"command":"echo hello"}',
                            },
                        }
                    ],
                }
                body["choices"][0]["finish_reason"] = "tool_calls"
                if status.get("tool_call_once"):
                    status["tool_call"] = False
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
    owned_transports = []

    def offline_scoped_transport(**kwargs):
        transport = transport_module.Client(
            transport=transport_module.MockTransport(handle), **kwargs
        )
        owned_transports.append(transport)
        return transport

    monkeypatch.setattr(
        anthropic if provider == "anthropic" else openai,
        "DefaultHttpxClient",
        offline_scoped_transport,
    )
    monkeypatch.setattr(llm, "init_llm", lambda provider: None)
    yield client, model, sent, status
    assert all(transport.is_closed for transport in owned_transports)
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


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_text_only_bound_counts_messages_not_routing_overhead(provider_client):
    import json

    _, model, sent, status = provider_client
    status["code"] = 200
    strict_reply(model)
    body = json.loads(sent[0].content)
    messages_only = len(
        json.dumps({"messages": body["messages"]}, ensure_ascii=False).encode()
    )
    assert len(sent[0].content) > messages_only
    assert strict_reply(model, max_input_bytes=messages_only).content == "ok"
    with pytest.raises(ValueError, match="serialized input byte"):
        strict_reply(model, max_input_bytes=messages_only - 1)
    assert len(sent) == 2


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
    import json

    from openai import NotFoundError

    _, model, sent, status = provider_client
    status["code"] = 404
    with pytest.raises(NotFoundError, match="No endpoints found"):
        strict_reply(model, max_provider_requests=2)
    assert len(sent) == 1
    assert json.loads(sent[0].content)["provider"]["require_parameters"] is True


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


@pytest.mark.parametrize("redirect_status", [307, 308])
def test_redirects_cannot_resend_strict_requests(provider_client, redirect_status):
    from anthropic import APIStatusError as AnthropicAPIStatusError
    from openai import APIStatusError as OpenAIAPIStatusError

    client, model, sent, status = provider_client
    client._client.follow_redirects = True
    status["code"] = redirect_status
    with pytest.raises((AnthropicAPIStatusError, OpenAIAPIStatusError)):
        strict_reply(model)
    assert len(sent) == 1
    assert sent[0].url.host != "unapproved.invalid"
    assert client._client.follow_redirects is True
    assert not client.is_closed()


def test_strict_call_uses_owned_transport_for_redirect_following_client(
    provider_client,
):
    client, model, sent, status = provider_client
    client._client.follow_redirects = True
    status["code"] = 200
    assert strict_reply(model).content == "ok"
    assert len(sent) == 1
    assert client._client.follow_redirects is True
    assert not client.is_closed()


def test_calls_outside_recovery_keep_redirect_behavior(provider_client):
    from gptme.llm.request_limits import bounded_client

    client, model, sent, status = provider_client
    client._client.follow_redirects = True
    status["code"] = 307
    with bounded_client(client, model.split("/")[0]) as send_client:
        assert send_client is client
        if model.startswith("anthropic/"):
            send_client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=64,
                messages=[{"role": "user", "content": "hi"}],
            )
        else:
            send_client.chat.completions.create(
                model="anthropic/claude-haiku-4.5",
                messages=[{"role": "user", "content": "hi"}],
            )
    assert len(sent) == 2
    assert sent[1].url.host == "unapproved.invalid"
    assert not client.is_closed()


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_native_tool_round_trip_shares_budget_and_bounds_output(provider_client):
    import json

    from gptme.llm.request_limits import request_limits
    from gptme.tools import get_tool, init_tools

    _, model, sent, status = provider_client
    init_tools(allowlist=["shell"])
    shell = get_tool("shell")
    assert shell is not None
    tools = [shell]
    status.update(code=200, tool_call=True)
    messages = [Message("system", "Use the tool"), Message("user", "say hello")]
    with request_limits(model, 20000, 2, max_tokens=64, native_tools=True):
        answer = llm.reply(messages, model, tools=tools)
        assert "call_offline" in answer.content
        messages += [answer, Message("system", "hello", call_id="call_offline")]
        status["tool_call"] = False
        assert llm.reply(messages, model, tools=tools, max_tokens=32).content == "ok"
        with pytest.raises(ValueError, match="request budget exhausted"):
            llm.reply(messages, model, tools=tools)
    assert len(sent) == 2
    first, second = [json.loads(request.content) for request in sent]
    assert first["max_tokens"] == 64
    assert second["max_tokens"] == 32
    assert first["tools"]
    assert second["messages"][-2]["tool_calls"][0]["id"] == "call_offline"
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-1]["tool_call_id"] == "call_offline"
    assert second["provider"]["require_parameters"] is True
    # Scope cleanup restores legacy one-reply accounting.
    assert strict_reply(model).content == "ok"
    assert len(sent) == 3


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_native_schema_overhead_rejected_before_http(provider_client):
    from gptme.llm.request_limits import request_limits
    from gptme.tools.base import Parameter, ToolSpec

    _, model, sent, status = provider_client
    status["code"] = 200
    tool = ToolSpec(
        name="save",
        desc="short",
        parameters=[
            Parameter(name="x", type="string", description="x" * 3000, required=True)
        ],
    )
    with (
        request_limits(model, 1024, 1, max_tokens=64, native_tools=True),
        pytest.raises(ValueError, match="serialized input byte"),
    ):
        llm.reply([Message("user", "a")], model, tools=[tool])
    assert not sent


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_retries_spend_remaining_conversation_budget(provider_client):
    from gptme.llm.request_limits import request_limits

    _, model, sent, status = provider_client
    with request_limits(model, 4096, 3, max_tokens=64, native_tools=True):
        status["code"] = 200
        llm.reply([Message("user", "first")], model)
        status["code"] = 503
        with pytest.raises(Exception, match="unavailable"):
            llm.reply([Message("user", "second")], model)
        assert len(sent) == 3
        with pytest.raises(ValueError, match="request budget exhausted"):
            llm.reply([Message("user", "third")], model)
    assert len(sent) == 3


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stream": True},
        {"max_tokens": 65},
        {"max_tokens": True},
        {"output_schema": dict},
        {"max_input_bytes": 4096, "max_provider_requests": 10},
        {"model": "openrouter/anthropic/claude-sonnet-4"},
        {"model": "anthropic/claude-haiku-4-5"},
        {"messages": [Message("user", "a", files=[Path(__file__)])]},
    ],
)
def test_native_scope_rejects_unsupported_replies_before_provider(monkeypatch, kwargs):
    from gptme.llm.request_limits import request_limits

    init = Mock()
    monkeypatch.setattr(llm, "init_llm", init)
    model = "openrouter/anthropic/claude-haiku-4.5"
    with (
        request_limits(model, 4096, 2, max_tokens=64, native_tools=True),
        pytest.raises(ValueError, match="strict request"),
    ):
        llm.reply(**{"messages": [Message("user", "a")], "model": model, **kwargs})
    init.assert_not_called()


@pytest.mark.parametrize(
    ("model", "tokens", "requests"),
    [
        ("anthropic/claude-haiku-4-5", 64, 1),
        ("openrouter/model", None, 1),
        ("openrouter/model", 0, 1),
        ("openrouter/model", True, 1),
        ("openrouter/model", 64, True),
    ],
)
def test_native_scope_rejects_invalid_configuration(model, tokens, requests):
    from gptme.llm.request_limits import request_limits

    with (
        pytest.raises(ValueError, match="strict request"),
        request_limits(model, 4096, requests, max_tokens=tokens, native_tools=True),
    ):
        pytest.fail("invalid scope admitted")


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_cli_scopes_provider_budget_and_cleans_up(
    provider_client, monkeypatch, tmp_path
):
    import importlib

    from click.testing import CliRunner

    from gptme.llm.request_limits import strict_request_bounds_active

    cli = importlib.import_module("gptme.cli.main")
    _, model, sent, status = provider_client
    status["code"] = 200

    def chat(*args, **kwargs):
        assert strict_request_bounds_active()
        assert args[5] is False
        for _ in range(2):
            assert llm.reply([Message("user", "a")], model).content == "ok"
        with pytest.raises(ValueError, match="request budget exhausted"):
            llm.reply([Message("user", "a")], model)

    monkeypatch.setattr(importlib.import_module("gptme.chat"), "chat", chat)
    from gptme.tools import clear_tools

    clear_tools()
    result = CliRunner().invoke(
        cli.main,
        [
            "--non-interactive",
            "--no-stream",
            "--tool-format",
            "tool",
            "--model",
            model,
            "--workspace",
            str(tmp_path),
            "--tools",
            "shell",
            "--max-input-bytes",
            "4096",
            "--max-provider-requests",
            "2",
            "--max-tokens",
            "64",
            "hello",
        ],
    )
    assert result.exit_code == 0, (
        result.output,
        result.exception,
        importlib.import_module("gptme.config").get_config().chat.tools,
    )
    assert len(sent) == 2
    assert not strict_request_bounds_active()


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--no-stream"],
        ["--no-stream", "--tool-format", "tool", "--model", "mock/test"],
    ],
)
def test_cli_rejects_unsupported_strict_route_before_setup(monkeypatch, extra):
    import importlib

    from click.testing import CliRunner

    cli = importlib.import_module("gptme.cli.main")
    setup = Mock()
    monkeypatch.setattr(
        importlib.import_module("gptme.config"), "setup_config_from_cli", setup
    )
    result = CliRunner().invoke(
        cli.main,
        [
            "--max-input-bytes",
            "4096",
            "--max-provider-requests",
            "2",
            "--max-tokens",
            "64",
            *extra,
            "hello",
        ],
    )
    assert result.exit_code == 2
    setup.assert_not_called()


def test_strict_conversation_disables_background_naming(tmp_path, monkeypatch):
    import importlib

    from gptme.config import ChatConfig
    from gptme.llm.request_limits import request_limits

    chat = importlib.import_module("gptme.chat")
    start = Mock()
    monkeypatch.setattr(chat.threading, "Thread", start)
    with request_limits("openrouter/test", 4096, 2, max_tokens=64, native_tools=True):
        chat._start_auto_naming_thread(
            tmp_path,
            ChatConfig(_logdir=tmp_path),
            [Message("user", "a")],
            "openrouter/test",
        )
    start.assert_not_called()
    assert tmp_path not in chat._naming_threads


def test_strict_native_rejects_subagent_before_provider(monkeypatch):
    from gptme.llm.request_limits import request_limits
    from gptme.tools import ToolSpec

    init = Mock()
    monkeypatch.setattr(llm, "init_llm", init)
    with (
        request_limits("openrouter/test", 4096, 2, max_tokens=64, native_tools=True),
        pytest.raises(ValueError, match="support only shell"),
    ):
        llm.reply(
            [Message("user", "a")],
            "openrouter/test",
            tools=[ToolSpec(name="subagent", desc="delegate")],
        )
    init.assert_not_called()


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_cli_native_tool_execution_and_exhaustion(
    provider_client, tmp_path, monkeypatch, request
):
    import importlib
    import json
    import signal

    from click.testing import CliRunner

    from gptme.llm.request_limits import strict_request_bounds_active
    from gptme.tools import clear_tools

    cli = importlib.import_module("gptme.cli.main")
    importlib.import_module("gptme.chat")
    clear_tools()
    _, model, sent, status = provider_client
    from gptme.llm.models import set_default_model

    set_default_model(model)
    # The CLI installs its own Ctrl-C handler; model, tools and cwd are
    # already reset between tests by conftest.
    previous_sigint = signal.getsignal(signal.SIGINT)
    request.addfinalizer(lambda: signal.signal(signal.SIGINT, previous_sigint))
    monkeypatch.setenv("GPTME_SHELL_MEMORY_GUARD", "0")
    status.update(code=200, tool_call=True, tool_call_once=True)
    result = CliRunner().invoke(
        cli.main,
        [
            "--non-interactive",
            "--no-stream",
            "--tool-format",
            "tool",
            "--model",
            model,
            "--workspace",
            str(tmp_path),
            "--tools",
            "shell",
            "--system",
            "Use shell once then finish.",
            "--no-workspace",
            "--max-input-bytes",
            "65536",
            "--max-provider-requests",
            "2",
            "--max-tokens",
            "64",
            "--",
            "Say hello.",
        ],
    )
    assert len(sent) == 2, (result.output, result.exception)
    second = json.loads(sent[1].content)
    assert any(
        message.get("role") == "tool" and message.get("tool_call_id") == "call_offline"
        for message in second["messages"]
    )
    assert "provider request budget exhausted" in result.output
    assert result.exit_code != 0
    assert not strict_request_bounds_active()


@pytest.mark.parametrize("prompt", ["search", "llm"])
def test_cli_bounds_cannot_disappear_through_command_shortcut(prompt):
    import importlib

    from click.testing import CliRunner

    cli = importlib.import_module("gptme.cli.main")
    result = CliRunner().invoke(cli.main, ["--max-provider-requests", "1", prompt])
    assert result.exit_code == 2
    assert "do not support command shortcuts" in result.output


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_native_scope_pins_concrete_api_model_for_direct_chat(provider_client):
    from gptme.llm import llm_openai
    from gptme.llm.request_limits import request_limits

    _, model, sent, status = provider_client
    status["code"] = 200
    with (
        request_limits(model, 4096, 2, max_tokens=64, native_tools=True),
        pytest.raises(ValueError, match="scoped API model"),
    ):
        llm_openai.chat(
            [Message("user", "a")],
            "openrouter/anthropic/claude-sonnet-4",
            None,
            max_tokens=64,
        )
    assert not sent


@pytest.mark.parametrize("provider_client", ["openrouter"], indirect=True)
def test_native_scope_rejects_direct_stream_before_http(provider_client):
    from gptme.llm import llm_openai
    from gptme.llm.request_limits import request_limits

    _, model, sent, status = provider_client
    status["code"] = 200
    with (
        request_limits(model, 4096, 2, max_tokens=64, native_tools=True),
        pytest.raises(ValueError, match="do not support streaming"),
    ):
        list(llm_openai.stream([Message("user", "a")], model, None, max_tokens=64))
    assert not sent


@pytest.mark.parametrize("value", ["64", "invalid"])
def test_output_token_environment_does_not_enable_strict_cli(monkeypatch, value):
    import importlib

    from click.testing import CliRunner

    cli = importlib.import_module("gptme.cli.main")
    monkeypatch.setenv("GPTME_MAX_TOKENS", value)
    result = CliRunner().invoke(cli.main, ["--version"])
    assert result.exit_code == 0, result.output
    # The existing generation override is still owned by the LLM layer.
    expected = 64 if value == "64" else llm._OPENROUTER_DEFAULT_MAX_TOKENS
    assert (
        llm._resolve_max_tokens("openrouter/anthropic/claude-haiku-4.5", None)
        == expected
    )


def test_output_token_environment_keeps_command_shortcuts(monkeypatch):
    import importlib

    from click.testing import CliRunner

    cli = importlib.import_module("gptme.cli.main")
    monkeypatch.setenv("GPTME_MAX_TOKENS", "64")
    monkeypatch.setattr(cli, "_same_env_executable", lambda name: "/offline/gptme-util")
    dispatch = Mock(return_value=0)
    monkeypatch.setattr(cli.subprocess, "call", dispatch)
    result = CliRunner().invoke(cli.main, ["llm", "generate", "hello"])
    assert result.exit_code == 0, result.output
    assert dispatch.call_args.args[0] == [
        "/offline/gptme-util",
        "llm",
        "generate",
        "hello",
    ]
