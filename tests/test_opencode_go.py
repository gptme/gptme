"""OpenCode Go uses the existing OpenAI transport with conversation affinity."""

from typing import Any
from unittest.mock import patch

import pytest

from gptme.config import get_config
from gptme.hooks.server_confirm import current_conversation_id
from gptme.llm import PROVIDER_API_KEYS, PROVIDER_DEFAULT_MODELS, llm_openai
from gptme.llm.models import get_model
from gptme.llm.models.types import PROVIDERS_OPENAI


def test_opencode_go_registration() -> None:
    assert "opencode-go" in PROVIDERS_OPENAI
    assert PROVIDER_API_KEYS["opencode-go"] == "OPENCODE_API_KEY"
    assert PROVIDER_DEFAULT_MODELS["opencode-go"] == "opencode-go/deepseek-v4.1-flash"
    for name in ("deepseek-v4.1-flash", "deepseek-v4-pro"):
        model = get_model(f"opencode-go/{name}")
        assert model.provider == "opencode-go"
        assert model.model == name
        assert model.context == 1_000_000
        assert model.supports_reasoning
        assert model.default_tool_format == "tool"
        # Extra Usage may charge the PAYG balance: do not report $0 marginal cost.
        assert model.pricing_type == "per_token"
        assert model.price_output > 0


def test_opencode_go_bare_provider_selects_default_model() -> None:
    model = get_model("opencode-go")
    assert model == get_model("opencode-go/deepseek-v4.1-flash")
    assert model.provider == "opencode-go"
    assert model.model == "deepseek-v4.1-flash"


def test_opencode_go_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "test-go-key")
    with (
        patch.object(llm_openai, "_init_openai_client") as create,
        patch.dict(llm_openai.clients, {"opencode-go": object()}),
    ):
        llm_openai.init("opencode-go", get_config())
    assert create.call_args.kwargs["api_key"] == "test-go-key"
    assert (
        create.call_args.kwargs["base_url"]
        == "https://opencode.ai/inference/go/openai/v1"
    )


def test_opencode_go_headers_use_server_conversation() -> None:
    token = current_conversation_id.set("conversation-one")
    try:
        first = llm_openai.extra_headers("opencode-go")
        assert first["User-Agent"].startswith("gptme/")
        assert first["x-opencode-session"]
        assert llm_openai.extra_headers("opencode-go") == first
        current_conversation_id.set("conversation-two")
        assert (
            llm_openai.extra_headers("opencode-go")["x-opencode-session"]
            != first["x-opencode-session"]
        )
    finally:
        current_conversation_id.reset(token)
    assert llm_openai.extra_headers("openai") == {}


def test_opencode_go_headers_use_cli_conversation() -> None:
    token = current_conversation_id.set(None)
    try:
        with patch(
            "gptme.telemetry.get_conversation_context", return_value=("cli-one", None)
        ):
            first = llm_openai.extra_headers("opencode-go")
            assert llm_openai.extra_headers("opencode-go") == first
        with patch(
            "gptme.telemetry.get_conversation_context", return_value=("cli-two", None)
        ):
            assert (
                llm_openai.extra_headers("opencode-go")["x-opencode-session"]
                != first["x-opencode-session"]
            )
    finally:
        current_conversation_id.reset(token)


def test_opencode_go_deepseek_tool_history() -> None:
    model = get_model("opencode-go/deepseek-v4.1-flash")
    history = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "<think>Inspect files</think>"}],
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": [{"type": "text", "text": "file.py"}],
        },
    ]
    result = list(llm_openai._transform_msgs_for_special_provider(history, model))
    assert result[0]["reasoning_content"] == "Inspect files"
    assert result[1]["content"] == "file.py"


@pytest.mark.parametrize(
    "model_name",
    [
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/deepseek-v4-pro",
        "deepseek/deepseek-reasoner",
    ],
)
def test_deepseek_tool_history_without_thinking(model_name: str) -> None:
    """A pending tool turn from a non-reasoning model can contain plain text."""
    model = get_model(model_name)
    history: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": "Inspect files",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "file.py"},
    ]
    result = list(llm_openai._transform_msgs_for_special_provider(history, model))
    assert result[0] == {**history[0], "reasoning_content": ""}
    assert result[1] == history[1]
    assert "reasoning_content" not in history[0]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("tool_history", [False, True])
def test_opencode_go_wire_request(streaming: bool, tool_history: bool) -> None:
    """Exercise the real SDK and gptme request path, without paid inference."""
    import json

    import httpx
    from openai import OpenAI

    from gptme.message import Message
    from gptme.tools import get_tool, init_tools

    init_tools(allowlist=["shell"])
    shell = get_tool("shell")
    assert shell
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if streaming:
            chunk = {
                "id": "test-go",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {"index": 0, "delta": {"content": "OK"}, "finish_reason": "stop"}
                ],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "test-go",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    with OpenAI(
        api_key="test-go-key",
        base_url="https://opencode.ai/inference/go/openai/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        token = current_conversation_id.set("wire-conversation")
        try:
            with patch.dict(llm_openai.clients, {"opencode-go": client}):
                args = (
                    [
                        Message("system", "You are helpful"),
                        Message("user", "Hello"),
                        Message("assistant", "Hello back"),
                        *(
                            [
                                Message("user", "Inspect files"),
                                Message(
                                    "assistant",
                                    'Inspect files\n@shell(call-1): {"command": "ls"}',
                                ),
                                Message("system", "file.py", call_id="call-1"),
                            ]
                            if tool_history
                            else []
                        ),
                        Message("user", "Say OK"),
                    ],
                    "opencode-go/deepseek-v4.1-flash",
                    [shell],
                )
                if streaming:
                    assert "OK" in "".join(llm_openai.stream(*args))
                else:
                    assert llm_openai.chat(*args)[0] == "OK"
            assert len(requests) == 1
            request = requests[0]
            assert (
                str(request.url)
                == "https://opencode.ai/inference/go/openai/v1/chat/completions"
            )
            assert request.headers["authorization"] == "Bearer test-go-key"
            assert request.headers["user-agent"].startswith("gptme/")
            assert (
                request.headers["x-opencode-session"]
                == llm_openai.extra_headers("opencode-go")["x-opencode-session"]
            )
            payload = json.loads(request.content)
            assert payload["model"] == "deepseek-v4.1-flash"
            assert payload["messages"] == [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hello back"},
                *(
                    [
                        {"role": "user", "content": "Inspect files"},
                        {
                            "role": "assistant",
                            "content": "Inspect files",
                            "reasoning_content": "",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "shell",
                                        "arguments": '{"command": "ls"}',
                                    },
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": "call-1",
                            "content": "file.py",
                        },
                    ]
                    if tool_history
                    else []
                ),
                {"role": "user", "content": "Say OK"},
            ]
        finally:
            current_conversation_id.reset(token)
