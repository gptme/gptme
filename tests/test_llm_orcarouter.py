"""Tests for the OrcaRouter provider wiring."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gptme.llm import (
    PROVIDER_API_KEYS,
    PROVIDER_DEFAULT_MODELS,
    get_model_from_api_key,
    llm_openai,
)
from gptme.llm.constants import ORCAROUTER_APP_HEADERS, ORCAROUTER_BASE_URL
from gptme.llm.models import MODELS, PROVIDERS, PROVIDERS_OPENAI, get_model
from gptme.message import Message


def _mock_client(monkeypatch):
    completion = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None),
            )
        ],
    )
    completions_create = Mock(return_value=completion)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completions_create))
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    return completions_create


def test_orcarouter_is_registered():
    assert "orcarouter" in PROVIDERS
    assert "orcarouter" in PROVIDERS_OPENAI
    assert PROVIDER_API_KEYS["orcarouter"] == "ORCAROUTER_API_KEY"
    assert PROVIDER_DEFAULT_MODELS["orcarouter"].startswith("orcarouter/")
    # the default must resolve against the static catalog
    assert get_model(PROVIDER_DEFAULT_MODELS["orcarouter"]).provider == "orcarouter"


def test_model_ids_keep_the_upstream_namespace():
    """The gateway rejects bare ids, so every static entry must be namespaced."""
    for name in MODELS["orcarouter"]:
        assert "/" in name, name

    model = get_model("orcarouter/anthropic/claude-sonnet-5")
    assert model.provider == "orcarouter"
    assert model.model == "anthropic/claude-sonnet-5"
    assert model.full == "orcarouter/anthropic/claude-sonnet-5"


def test_sk_orca_key_is_not_mistaken_for_openai():
    """`sk-orca-…` also matches the generic `sk-` prefix, so order matters."""
    assert get_model_from_api_key("sk-orca-abc123") == (
        "sk-orca-abc123",
        "orcarouter",
        "ORCAROUTER_API_KEY",
    )
    openai_guess = get_model_from_api_key("sk-abc123")
    openrouter_guess = get_model_from_api_key("sk-or-abc123")
    assert openai_guess is not None and openai_guess[1] == "openai"
    assert openrouter_guess is not None and openrouter_guess[1] == "openrouter"


def test_attribution_headers_are_sent():
    assert llm_openai.extra_headers("orcarouter") == ORCAROUTER_APP_HEADERS


def test_extra_body_is_empty_without_thinking_effort(monkeypatch):
    monkeypatch.delenv("GPTME_THINKING_EFFORT", raising=False)
    model = get_model("orcarouter/openai/gpt-5.5")
    assert llm_openai.extra_body("orcarouter", model, max_tokens=None) == {}


def test_extra_body_uses_flat_reasoning_effort(monkeypatch):
    """OrcaRouter takes a top-level `reasoning_effort`, not OpenRouter's
    nested `reasoning` object — so none of the OpenRouter keys may leak in."""
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "xhigh")
    model = get_model("orcarouter/openai/gpt-5.5")
    body = llm_openai.extra_body("orcarouter", model, max_tokens=20_000)
    assert body == {"reasoning_effort": "xhigh"}
    assert "reasoning" not in body
    assert "provider" not in body


def test_extra_body_rejects_unsupported_reasoning_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "max")
    with pytest.raises(ValueError, match="OrcaRouter reasoning effort"):
        llm_openai.extra_body(
            "orcarouter", get_model("orcarouter/openai/gpt-5.5"), max_tokens=None
        )


def test_anthropic_upstreams_omit_top_p():
    """Some Anthropic upstreams 400 when both temperature and top_p are set."""
    claude = get_model("orcarouter/anthropic/claude-sonnet-5")
    gpt = get_model("orcarouter/openai/gpt-5.6-luna")

    assert llm_openai._get_top_p("orcarouter", claude, top_p=0.82) is None
    assert llm_openai._get_temperature("orcarouter", claude, temperature=0.37) == 0.37
    # non-Anthropic upstreams are unaffected (gpt-5* drops top_p on its own)
    assert llm_openai._get_top_p("orcarouter", gpt, top_p=0.82) is None
    assert llm_openai._get_top_p("orcarouter", None, top_p=0.82) == 0.82


def test_chat_sends_namespaced_model_and_headers(monkeypatch):
    completions_create = _mock_client(monkeypatch)

    result, _ = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "orcarouter/openai/gpt-5.6-luna",
        None,
    )

    assert result == "ok"
    kwargs = completions_create.call_args.kwargs
    assert kwargs["model"] == "openai/gpt-5.6-luna"
    assert kwargs["extra_headers"] == ORCAROUTER_APP_HEADERS


def test_init_uses_the_gateway_base_url(monkeypatch):
    from gptme.config import get_config

    monkeypatch.setenv("ORCAROUTER_API_KEY", "sk-orca-test")
    monkeypatch.delenv("LLM_PROXY_URL", raising=False)
    monkeypatch.delenv("LLM_PROXY_API_KEY", raising=False)

    created: dict = {}

    def fake_factory(name):
        def factory(**kwargs):
            created.update(kwargs)
            return SimpleNamespace(**kwargs)

        return factory

    monkeypatch.setattr(llm_openai, "_lazy_client_factory", fake_factory)
    llm_openai.init("orcarouter", get_config())

    assert created["base_url"] == ORCAROUTER_BASE_URL
    assert created["api_key"] == "sk-orca-test"


def test_tools_api_is_supported():
    """`orcarouter` must be in the tools allow-list, or tool use raises."""
    from gptme.tools.base import Parameter, ToolSpec

    spec = ToolSpec(
        name="shell",
        desc="Run a shell command",
        parameters=[Parameter(name="command", type="string", description="cmd")],
    )
    tool = llm_openai._spec2tool(spec, get_model("orcarouter/openai/gpt-5.6-luna"))
    assert tool["function"]["name"] == "shell"


@pytest.mark.parametrize(
    ("endpoint_types", "expected"),
    [
        (["openai"], True),
        (["openai", "anthropic"], True),
        (["openai-response"], True),
        (["anthropic", "gemini"], False),
        (["openai-video"], False),
        (["image-generation"], False),
        (["embeddings"], False),
        # unclassified entries (null / missing / empty) are kept
        (None, True),
        ([], True),
    ],
)
def test_non_chat_models_are_filtered_from_the_catalog(endpoint_types, expected):
    """`kling/kling-v3` is a video model with no keyword in its id, so the
    listing has to classify on supported_endpoint_types instead."""
    entry = {"id": "some/model"}
    if endpoint_types is not None:
        entry["supported_endpoint_types"] = endpoint_types
    assert llm_openai._orcarouter_is_chat_model(entry) is expected


def test_listed_models_keep_the_builtin_provider():
    meta = llm_openai._openai_compatible_model_to_modelmeta(
        {"id": "openai/gpt-5.6-luna"}, "orcarouter"
    )
    assert meta.provider == "orcarouter"
    assert meta.full == "orcarouter/openai/gpt-5.6-luna"
