"""MiniMax catalog and wire-contract regressions (no live inference)."""

import inspect
import json
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from gptme.config import get_config
from gptme.config.models import ProviderConfig
from gptme.llm import llm_openai, provider_plugins
from gptme.llm.models import (
    CustomProvider,
    ModelMeta,
    Provider,
    ProviderPlugin,
    get_model,
)
from gptme.llm.models.listing import get_model_list
from gptme.message import Message
from gptme.tools import ToolSpec

FLASH = "minimax/minimax-m3.1-flash:free"
M3 = "minimax/minimax-m3"
LEGACY = "minimax/minimax-m2.7"
Catalog = list[dict[str, Any]]
Wire = list[tuple[httpx.Request, dict[str, Any]]]


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> Iterator[Catalog]:
    # Keep the gateway's context/output/pricing separate from direct API values.
    raw = [
        {
            "id": name,
            "context_length": context,
            "top_provider": {"max_completion_tokens": output},
            "pricing": {"prompt": price, "completion": "0"},
            "supported_parameters": ["reasoning", "include_reasoning", "tools"],
            "architecture": {"input_modalities": ["text", "image", "video"]},
        }
        for name, context, output, price in [
            (M3, 1_048_576, 512_000, "0.0000003"),
            ("other/model", 128_000, 4096, "0"),
            (LEGACY, 204_800, 131_072, "0.0000003"),
            (FLASH, 1_000_000, 524_288, "0"),
        ]
    ]
    response = Mock()
    response.json.return_value = {"data": raw}
    monkeypatch.setattr(llm_openai.requests, "get", Mock(return_value=response))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("GPTME_THINKING_EFFORT", raising=False)
    monkeypatch.setattr("gptme.plugins.registry._initialized", False)
    monkeypatch.setattr(provider_plugins, "_discover_provider_plugins_raw", lambda: ())
    llm_openai.get_available_models.cache_clear()
    yield raw
    llm_openai.get_available_models.cache_clear()


def test_gateway_active_choices_and_saved_legacy(catalog: Catalog) -> None:
    models = get_model_list("openrouter")
    assert [m.model for m in models] == [FLASH, "other/model", M3]
    assert {m.model for m in get_model_list("openrouter", include_deprecated=True)} == {
        FLASH,
        M3,
        LEGACY,
        "other/model",
    }
    assert get_model(f"openrouter/{LEGACY}").model == LEGACY
    flash, _, m3 = models
    assert (flash.context, flash.max_output, flash.price_input) == (
        1_000_000,
        524_288,
        0,
    )
    assert (m3.context, m3.max_output, m3.price_input) == (1_048_576, 512_000, 0.3)
    assert flash.supports_vision and flash.supports_reasoning


def test_custom_catalog_is_not_curated(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = ModelMeta(provider="unknown", model="minimax/MiniMax-M2.7", context=42)
    plugin = ProviderPlugin(name="minimax", models=[custom])
    monkeypatch.setattr(
        provider_plugins, "_discover_provider_plugins_raw", lambda: (plugin,)
    )
    assert get_model_list("minimax") == [custom]
    assert get_model(custom.model) is custom


@pytest.fixture
def plugin_example(catalog: Catalog, monkeypatch: pytest.MonkeyPatch) -> ProviderPlugin:
    # Execute the shipped recipe so the advertised IDs/metadata are exercised.
    doc = inspect.getdoc(ProviderPlugin)
    assert doc is not None
    source = doc[
        doc.index("    from gptme.llm.models") : doc.index("For providers that")
    ]
    namespace: dict[str, Any] = {}
    exec(textwrap.dedent(source), namespace)
    plugin = namespace["provider"]
    monkeypatch.setattr(
        provider_plugins, "_discover_provider_plugins_raw", lambda: (plugin,)
    )
    assert [m.model for m in plugin.models] == [
        "minimax/MiniMax-M3.1-Flash",
        "minimax/MiniMax-M3",
    ]
    assert plugin.base_url == "https://api.minimax.io/v1"
    assert plugin.models[0].max_output == 524_288
    return plugin


@pytest.fixture
def wire(
    plugin_example: ProviderPlugin, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Wire]:
    captured: Wire = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append((request, body))
        if body.get("stream"):
            chunk = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {"index": 0, "delta": {"content": "ok"}, "finish_reason": None}
                ],
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
            )
        message: dict[str, Any] = {"role": "assistant", "content": "ok"}
        if body.get("tools") and not any(m["role"] == "tool" for m in body["messages"]):
            message = {
                "role": "assistant",
                "content": None,
                "reasoning_content": "Use the tool.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "probe", "arguments": "{}"},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls"
                        if "tool_calls" in message
                        else "stop",
                    }
                ],
            },
        )

    def factory(_class_name: str) -> Callable[..., OpenAI]:
        def create(**kwargs: Any) -> OpenAI:
            if isinstance(kwargs.get("timeout"), llm_openai._UnsetTimeout):
                kwargs.pop("timeout")
            return OpenAI(
                **kwargs,
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(respond)),
            )

        return create

    # Let production init choose credentials/endpoints; replace only HTTP transport.
    clients: dict[Provider, OpenAI] = {}
    monkeypatch.setattr(llm_openai, "clients", clients)
    monkeypatch.setattr(llm_openai, "_lazy_client_factory", factory)
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    llm_openai.init("openrouter", get_config())
    llm_openai.init(CustomProvider("minimax"), get_config())
    yield captured
    for client in clients.values():
        client.close()


@pytest.mark.parametrize("model", [FLASH, M3, LEGACY])
@pytest.mark.parametrize("stream", [False, True])
def test_gateway_wire_model_and_endpoint(
    catalog: Catalog, wire: Wire, model: str, stream: bool
) -> None:
    messages = [Message("user", "hello")]
    if stream:
        list(llm_openai.stream(messages, f"openrouter/{model}", None, max_tokens=8192))
    else:
        llm_openai.chat(messages, f"openrouter/{model}", None, max_tokens=8192)
    request, body = wire[-1]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == model
    if model == FLASH:
        assert body["reasoning"] == {"effort": "max"}
        assert "service_tier" not in body and "reasoning_split" not in body


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_flash_effort_on_wire(
    catalog: Catalog, wire: Wire, monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    monkeypatch.setenv("GPTME_THINKING_EFFORT", effort)
    llm_openai.chat([Message("user", "hello")], f"openrouter/{FLASH}", None)
    assert wire[-1][1]["reasoning"] == {"effort": effort}


@pytest.mark.parametrize("effort", ["none", "minimal", "disabled", "invalid"])
def test_flash_rejects_unsupported_effort_before_send(
    catalog: Catalog, wire: Wire, monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    monkeypatch.setenv("GPTME_THINKING_EFFORT", effort)
    with pytest.raises(ValueError, match="MiniMax-M3.1-Flash"):
        llm_openai.chat([Message("user", "hello")], f"openrouter/{FLASH}", None)
    assert not wire


@pytest.mark.parametrize(
    "model",
    [
        f"openrouter/{FLASH}",
        f"openrouter/{M3}",
        "minimax/MiniMax-M3.1-Flash",
        "minimax/MiniMax-M3",
    ],
)
def test_tool_reasoning_round_trip(
    plugin_example: ProviderPlugin, wire: Wire, model: str
) -> None:
    tools = [ToolSpec(name="probe", desc="Return a test result")]
    messages = [Message("user", "Use the tool")]
    first, _ = llm_openai.chat(messages, model, tools)
    assert "Use the tool." in first and "@probe(call_1)" in first
    messages += [
        Message("assistant", first),
        Message("system", "done", call_id="call_1"),
    ]
    llm_openai.chat(messages, model, tools)
    request, body = wire[-1]
    assistant = next(m for m in body["messages"] if m["role"] == "assistant")
    assert assistant["reasoning_content"].strip() == "Use the tool."
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert any(m.get("tool_call_id") == "call_1" for m in body["messages"])
    if model.startswith("minimax/"):
        assert str(request.url) == "https://api.minimax.io/v1/chat/completions"
        assert body["model"] == model.split("/", 1)[1]
        assert "reasoning" not in body and "service_tier" not in body
        assert "reasoning_split" not in body


@pytest.mark.parametrize("model", [f"openrouter/{FLASH}", "minimax/MiniMax-M3.1-Flash"])
def test_image_preserved_on_wire(
    plugin_example: ProviderPlugin, wire: Wire, tmp_path: Path, model: str
) -> None:
    image = tmp_path / "test.png"
    Image.new("RGB", (1, 1), "white").save(image)
    llm_openai.chat([Message("user", "Describe", files=[image])], model, None)
    parts = wire[-1][1]["messages"][0]["content"]
    images = [part for part in parts if part["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.minimax.io/v1",
        "https://api.minimax.chat/v1",
        "https://private.example.test/custom/v1",
    ],
)
def test_explicit_plugin_endpoint_and_legacy_id(
    plugin_example: ProviderPlugin, wire: Wire, base_url: str
) -> None:
    plugin_example.base_url = base_url
    llm_openai.clients[CustomProvider("minimax")].close()
    llm_openai.init(CustomProvider("minimax"), get_config())
    llm_openai.chat([Message("user", "hello")], "minimax/abab6.5s-chat", None)
    request, body = wire[-1]
    assert str(request.url) == base_url + "/chat/completions"
    assert body["model"] == "abab6.5s-chat"
    assert "reasoning" not in body and "reasoning_effort" not in body


def test_saved_custom_provider_request(
    wire: Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = get_config()
    custom = ProviderConfig(
        name="saved-minimax",
        base_url="https://private.example.test/v1",
        api_key="custom-test-key",
        default_model="MiniMax-M2.7",
    )
    monkeypatch.setattr(config.user, "providers", [custom])
    llm_openai.init(CustomProvider(custom.name), config)
    llm_openai.chat([Message("user", "hello")], "saved-minimax/MiniMax-M2.7", None)
    request, body = wire[-1]
    assert str(request.url) == custom.base_url + "/chat/completions"
    assert request.headers["authorization"] == "Bearer custom-test-key"
    assert body["model"] == custom.default_model
    assert "reasoning" not in body and "reasoning_effort" not in body
    assert config.user.providers == [custom]
