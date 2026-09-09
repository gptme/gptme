"""GPTME_THINKING_EFFORT for OpenAI-compatible providers + reasoning observability.

Covers the request side (``extra_body`` / Responses ``reasoning.effort``) and
the record side (``metadata.reasoning_effort``, ``metadata.usage.reasoning_tokens``).
"""

from types import SimpleNamespace
from typing import get_args
from unittest.mock import Mock

import pytest

from gptme.llm import llm_openai
from gptme.llm.llm_openai import (
    _OPENAI_REASONING_EFFORTS,
    _record_usage,
    _resolve_reasoning_effort,
    extra_body,
)
from gptme.llm.models import get_model
from gptme.llm.models.types import ModelMeta
from gptme.message import Message


def _meta(model: str, provider: str, supports_reasoning: bool) -> ModelMeta:
    return ModelMeta(
        provider=provider,  # type: ignore[arg-type]
        model=model,
        context=128000,
        supports_reasoning=supports_reasoning,
    )


def _chat_usage(reasoning_tokens=None):
    from openai.types.completion_usage import CompletionUsage

    raw: dict = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    if reasoning_tokens is not None:
        raw["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}
    return CompletionUsage.model_validate(raw)


def _responses_usage(reasoning_tokens=None):
    from openai.types.responses.response_usage import ResponseUsage

    # The SDK model requires both details blocks; OpenAI always sends them.
    raw: dict = {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens or 0},
    }
    return ResponseUsage.model_validate(raw)


def _mock_chat_client(monkeypatch, completion):
    raw_resp = SimpleNamespace(parse=lambda: completion, headers={})
    create = Mock(return_value=raw_resp)
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=create)
            )
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    return create


def _mock_responses_client(monkeypatch, response):
    create = Mock(return_value=response)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: True)
    return create


def test_openai_effort_set_matches_sdk_literal():
    """Drift guard: every level we accept must be one the openai SDK types."""
    from openai.types.shared.reasoning_effort import ReasoningEffort

    sdk_levels = set(get_args(get_args(ReasoningEffort)[0]))
    assert sdk_levels >= _OPENAI_REASONING_EFFORTS


# --- request side: extra_body / _resolve_reasoning_effort ---------------------


def test_unset_env_leaves_bodies_untouched(monkeypatch):
    monkeypatch.delenv("GPTME_THINKING_EFFORT", raising=False)
    assert extra_body("openai", _meta("gpt-5", "openai", True)) == {}
    assert _resolve_reasoning_effort("openai", _meta("gpt-5", "openai", True)) is None
    body = extra_body("openrouter", _meta("openai/o3", "openrouter", True))
    assert body["reasoning"] == {"enabled": True, "max_tokens": 20000}


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh"])
def test_openai_chat_sends_reasoning_effort(monkeypatch, level):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", level.upper())
    assert extra_body("openai", _meta("gpt-5", "openai", True)) == {
        "reasoning_effort": level
    }


def test_openai_non_reasoning_model_ignores_effort(monkeypatch):
    """gpt-4o rejects reasoning_effort with 400 — never send it there."""
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    assert extra_body("openai", _meta("gpt-4o", "openai", False)) == {}
    assert _resolve_reasoning_effort("openai", _meta("gpt-4o", "openai", False)) is None


def test_openai_rejects_unknown_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "extreme")
    with pytest.raises(ValueError, match="Invalid OpenAI reasoning effort.*'extreme'"):
        extra_body("openai", _meta("gpt-5", "openai", True))


def test_openrouter_effort_replaces_budget(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    body = extra_body(
        "openrouter", _meta("openai/o3", "openrouter", True), max_tokens=64
    )
    assert body["reasoning"] == {"effort": "high"}
    assert body["usage"] == {"include": True}
    # reasoning present → same provider-pref interplay as the budget path
    assert "require_parameters" not in body["provider"]
    assert "data_collection" not in body["provider"]


def test_openrouter_non_reasoning_model_ignores_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    body = extra_body("openrouter", _meta("meta-llama/llama-3.1", "openrouter", False))
    assert "reasoning" not in body
    assert body["provider"]["require_parameters"] is True


def test_openrouter_rejects_unknown_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "max")
    with pytest.raises(ValueError, match="Invalid OpenRouter reasoning effort"):
        extra_body("openrouter", _meta("openai/o3", "openrouter", True))


def test_kimi_k3_special_case_intact(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "max")
    assert extra_body("moonshot", get_model("moonshot/kimi-k3")) == {
        "reasoning_effort": "max"
    }
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "medium")
    with pytest.raises(ValueError, match="Kimi K3 reasoning effort"):
        extra_body("moonshot", get_model("moonshot/kimi-k3"))


def test_other_providers_ignore_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    assert (
        _resolve_reasoning_effort(
            "deepseek", _meta("deepseek-reasoner", "deepseek", True)
        )
        is None
    )
    assert extra_body("deepseek", _meta("deepseek-reasoner", "deepseek", True)) == {}


# --- record side: _record_usage -------------------------------------------------


def test_record_usage_chat_reasoning_tokens_present():
    metadata = _record_usage(_chat_usage(reasoning_tokens=30), "openai/gpt-5")
    assert metadata is not None
    assert metadata["usage"]["reasoning_tokens"] == 30
    assert metadata["usage"]["output_tokens"] == 50
    assert "reasoning_effort" not in metadata


def test_record_usage_responses_reasoning_tokens_present():
    metadata = _record_usage(_responses_usage(reasoning_tokens=12), "openai/gpt-5")
    assert metadata is not None
    assert metadata["usage"]["reasoning_tokens"] == 12


def test_record_usage_reasoning_tokens_absent():
    metadata = _record_usage(_chat_usage(), "openai/gpt-4o")
    assert metadata is not None
    assert "reasoning_tokens" not in metadata["usage"]


def test_record_usage_openrouter_dict_usage_reasoning_tokens():
    """OpenRouter usage accounting (raw dict shape from SSE) carries the same field."""
    usage = {
        "prompt_tokens": 10,
        "completion_tokens": 40,
        "completion_tokens_details": {"reasoning_tokens": 25},
    }
    metadata = _record_usage(usage, "openrouter/openai/o3")
    assert metadata is not None
    assert metadata["usage"]["reasoning_tokens"] == 25


def test_record_usage_stamps_effort_with_and_without_usage():
    with_usage = _record_usage(_chat_usage(), "openai/gpt-5", reasoning_effort="high")
    assert with_usage is not None
    assert with_usage["reasoning_effort"] == "high"
    assert _record_usage(None, "openai/gpt-5", reasoning_effort="low") == {
        "model": "openai/gpt-5",
        "reasoning_effort": "low",
    }
    assert _record_usage(None, "openai/gpt-5") is None


# --- end to end: chat()/stream() ------------------------------------------------


def test_chat_completions_stamps_effort_and_reasoning_tokens(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    completion = SimpleNamespace(
        usage=_chat_usage(reasoning_tokens=7),
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None),
            )
        ],
    )
    create = _mock_chat_client(monkeypatch, completion)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)

    result, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-5", None
    )

    assert result == "ok"
    assert create.call_args.kwargs["extra_body"] == {"reasoning_effort": "high"}
    assert metadata is not None
    assert metadata["reasoning_effort"] == "high"
    assert metadata["usage"]["reasoning_tokens"] == 7


def test_chat_responses_path_sends_effort_and_stamps(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "xhigh")
    response = SimpleNamespace(
        usage=_responses_usage(reasoning_tokens=99),
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="ok")],
            )
        ],
    )
    create = _mock_responses_client(monkeypatch, response)

    result, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-5", None
    )

    assert result == "ok"
    assert create.call_args.kwargs["reasoning"] == {"effort": "xhigh"}
    assert metadata is not None
    assert metadata["reasoning_effort"] == "xhigh"
    assert metadata["usage"]["reasoning_tokens"] == 99


def test_chat_responses_path_unset_env_sends_no_reasoning(monkeypatch):
    monkeypatch.delenv("GPTME_THINKING_EFFORT", raising=False)
    response = SimpleNamespace(
        usage=None,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="ok")],
            )
        ],
    )
    create = _mock_responses_client(monkeypatch, response)

    _, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-5", None
    )

    assert "reasoning" not in create.call_args.kwargs
    assert metadata is None


def test_chat_invalid_effort_fails_before_request(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "bogus")
    create = _mock_chat_client(monkeypatch, SimpleNamespace(usage=None, choices=[]))
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)

    with pytest.raises(ValueError, match="Invalid OpenAI reasoning effort"):
        llm_openai.chat([Message(role="user", content="hi")], "openai/gpt-5", None)
    create.assert_not_called()


def _drain(gen):
    parts: list[str] = []
    while True:
        try:
            parts.append(next(gen))
        except StopIteration as exc:
            return "".join(parts), exc.value


def test_stream_completions_stamps_effort_without_usage_chunk(monkeypatch):
    """A stream that never reports usage still records the requested effort."""
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "low")
    chunk = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                delta=SimpleNamespace(
                    reasoning_content=None,
                    reasoning=None,
                    content="ok",
                    tool_calls=None,
                ),
            )
        ],
    )
    create = Mock(return_value=[chunk])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)

    text, metadata = _drain(
        llm_openai.stream([Message(role="user", content="hi")], "openai/gpt-5", None)
    )

    assert text == "ok"
    assert create.call_args.kwargs["extra_body"] == {"reasoning_effort": "low"}
    assert metadata == {"model": "openai/gpt-5", "reasoning_effort": "low"}


def test_stream_responses_sends_effort_and_records_reasoning_tokens(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "medium")
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="ok"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=_responses_usage(reasoning_tokens=5)),
        ),
    ]
    create = Mock(return_value=events)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    text, metadata = _drain(
        llm_openai._stream_responses(
            [Message(role="user", content="hi")],
            "openai/gpt-5",
            None,
            get_model("openai/gpt-5"),
        )
    )

    assert text == "ok"
    assert create.call_args.kwargs["reasoning"] == {"effort": "medium"}
    assert metadata is not None
    assert metadata["reasoning_effort"] == "medium"
    assert metadata["usage"]["reasoning_tokens"] == 5
