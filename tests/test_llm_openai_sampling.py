from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from gptme.llm import llm_openai
from gptme.llm.models import get_model
from gptme.message import Message


def _collect_stream_result(generator):
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(generator))
        except StopIteration as exc:
            return "".join(chunks), exc.value


def test_sampling_helpers_preserve_caller_values_for_standard_models():
    model_meta = get_model("openai/gpt-4o")

    assert llm_openai._get_temperature("openai", model_meta, temperature=0.37) == 0.37
    assert llm_openai._get_top_p("openai", model_meta, top_p=0.82) == 0.82


def test_sampling_helpers_keep_model_overrides():
    gpt5 = get_model("openai/gpt-5")
    moonshot = get_model("moonshot/kimi-k2.6")

    assert llm_openai._get_temperature("openai", gpt5, temperature=0.37) == 1.0
    assert llm_openai._get_top_p("openai", gpt5, top_p=0.82) is None
    assert llm_openai._get_temperature("moonshot", moonshot, temperature=0.37) == 1.0
    assert llm_openai._get_top_p("moonshot", moonshot, top_p=0.82) == 0.95


def test_kimi_k3_sends_configured_reasoning_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "high")
    completion = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="answer",
                    reasoning_content="reasoning",
                    tool_calls=None,
                ),
            )
        ],
    )
    raw_resp = SimpleNamespace(parse=lambda: completion, headers={})
    completions_create = Mock(return_value=raw_resp)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=completions_create)
            )
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    result, _ = llm_openai.chat(
        [Message(role="user", content="Solve this.")],
        "moonshot/kimi-k3",
        None,
    )

    assert result == "<think>\nreasoning\n</think>\n\nanswer"
    assert "temperature" not in completions_create.call_args.kwargs
    assert "top_p" not in completions_create.call_args.kwargs
    assert completions_create.call_args.kwargs["extra_body"] == {
        "reasoning_effort": "high"
    }


def test_kimi_k3_rejects_unsupported_reasoning_effort(monkeypatch):
    monkeypatch.setenv("GPTME_THINKING_EFFORT", "medium")

    with pytest.raises(ValueError, match="Kimi K3 reasoning effort"):
        llm_openai.extra_body(
            "moonshot", get_model("moonshot/kimi-k3"), max_tokens=None
        )


def test_chat_completions_forwards_caller_sampling_values(monkeypatch):
    completion = SimpleNamespace(
        usage=None,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None),
            )
        ],
    )
    raw_resp = SimpleNamespace(parse=lambda: completion, headers={})
    completions_create = Mock(return_value=raw_resp)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=completions_create)
            )
        )
    )

    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    result, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openai/gpt-4o",
        None,
        temperature=0.37,
        top_p=0.82,
    )

    assert result == "ok"
    assert metadata is None
    assert completions_create.call_args.kwargs["temperature"] == 0.37
    assert completions_create.call_args.kwargs["top_p"] == 0.82


def test_chat_responses_path_forwards_caller_sampling_values(monkeypatch):
    response = SimpleNamespace(
        usage=None,
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="ok")],
            )
        ],
    )
    responses_create = Mock(return_value=response)
    mock_client = SimpleNamespace(responses=SimpleNamespace(create=responses_create))

    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *args: True)

    result, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openai/gpt-4o",
        None,
        temperature=0.31,
        top_p=0.76,
    )

    assert result == "ok"
    assert metadata is None
    assert responses_create.call_args.kwargs["temperature"] == 0.31
    assert responses_create.call_args.kwargs["top_p"] == 0.76


def test_stream_completions_forwards_caller_sampling_values(monkeypatch):
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
    completions_create = Mock(return_value=[chunk])
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completions_create))
    )

    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    text, metadata = _collect_stream_result(
        llm_openai.stream(
            [Message(role="user", content="Say ok.")],
            "openai/gpt-4o",
            None,
            temperature=0.23,
            top_p=0.74,
        )
    )

    assert text == "ok"
    assert metadata is None
    assert completions_create.call_args.kwargs["temperature"] == 0.23
    assert completions_create.call_args.kwargs["top_p"] == 0.74


@pytest.mark.parametrize("include_usage", [True, False])
def test_stream_captures_openrouter_provider_in_metadata(monkeypatch, include_usage):
    chunks = []
    if include_usage:
        usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)
        chunks.append(SimpleNamespace(usage=usage, choices=[]))
    stream_obj = MagicMock()
    stream_obj.response.headers.get.return_value = "Together AI"
    stream_obj.__iter__.return_value = iter(chunks)
    completions_create = Mock(return_value=stream_obj)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completions_create))
    )

    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    _, metadata = _collect_stream_result(
        llm_openai.stream(
            [Message(role="user", content="Say ok.")],
            "openrouter/meta-llama/llama-3.1",
            None,
        )
    )

    assert metadata is not None
    assert metadata["resolved_model"] == ("openrouter/meta-llama/llama-3.1@together-ai")
    if include_usage:
        assert metadata["usage"] == {"input_tokens": 3, "output_tokens": 2}
    else:
        assert "usage" not in metadata
    stream_obj.response.headers.get.assert_called_once_with("x-openrouter-provider")


def test_stream_responses_forwards_caller_sampling_values(monkeypatch):
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="ok"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(usage=None),
        ),
    ]
    responses_create = Mock(return_value=events)
    mock_client = SimpleNamespace(responses=SimpleNamespace(create=responses_create))

    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    text, metadata = _collect_stream_result(
        llm_openai._stream_responses(
            [Message(role="user", content="Say ok.")],
            "openai/gpt-4o",
            None,
            get_model("openai/gpt-4o"),
            temperature=0.19,
            top_p=0.67,
        )
    )

    assert text == "ok"
    assert metadata is None
    assert responses_create.call_args.kwargs["temperature"] == 0.19
    assert responses_create.call_args.kwargs["top_p"] == 0.67


def _openrouter_completion(provider: str | None):
    """A real SDK ChatCompletion, optionally carrying OpenRouter's body ``provider``."""
    from openai.types.chat import ChatCompletion

    data: dict = {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": "meta-llama/llama-3.1",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "ok"},
            }
        ],
    }
    if provider is not None:
        data["provider"] = provider
    return ChatCompletion.model_validate(data)


def _openrouter_chunks(provider: str | None, include_usage: bool):
    """Real SDK ChatCompletionChunks, optionally carrying the body ``provider``."""
    from openai.types.chat import ChatCompletionChunk

    def chunk(**extra):
        data: dict = {
            "id": "gen-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "meta-llama/llama-3.1",
            **extra,
        }
        if provider is not None:
            data["provider"] = provider
        return ChatCompletionChunk.model_validate(data)

    chunks = [
        chunk(
            choices=[{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]
        )
    ]
    if include_usage:
        chunks.append(
            chunk(
                choices=[],
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        )
    return chunks


def _mock_raw_chat_client(monkeypatch, completion, headers):
    raw_resp = SimpleNamespace(parse=lambda: completion, headers=headers)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=Mock(return_value=raw_resp))
            )
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)


def test_chat_falls_back_to_body_provider_without_header(monkeypatch):
    """Proxies that drop x-openrouter-provider still get attribution from the body."""
    _mock_raw_chat_client(monkeypatch, _openrouter_completion("Together"), {})

    _, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openrouter/meta-llama/llama-3.1",
        None,
    )

    assert metadata is not None
    assert metadata["resolved_model"] == "openrouter/meta-llama/llama-3.1@together"


def test_chat_prefers_header_over_body_provider(monkeypatch):
    _mock_raw_chat_client(
        monkeypatch,
        _openrouter_completion("Together"),
        {"x-openrouter-provider": "Groq"},
    )

    _, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openrouter/meta-llama/llama-3.1",
        None,
    )

    assert metadata is not None
    assert metadata["resolved_model"] == "openrouter/meta-llama/llama-3.1@groq"


def test_chat_without_header_or_body_provider_has_no_resolved_model(monkeypatch):
    _mock_raw_chat_client(monkeypatch, _openrouter_completion(None), {})

    _, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openrouter/meta-llama/llama-3.1",
        None,
    )

    assert not metadata or "resolved_model" not in metadata


def test_chat_ignores_body_provider_for_non_openrouter(monkeypatch):
    _mock_raw_chat_client(monkeypatch, _openrouter_completion("Together"), {})

    _, metadata = llm_openai.chat(
        [Message(role="user", content="Say ok.")],
        "openai/gpt-4o",
        None,
    )

    assert not metadata or "resolved_model" not in metadata


def _mock_stream_client(monkeypatch, chunks, header):
    stream_obj = MagicMock()
    stream_obj.response.headers.get.return_value = header
    stream_obj.__iter__.return_value = iter(chunks)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=stream_obj))
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)


@pytest.mark.parametrize("include_usage", [True, False])
def test_stream_falls_back_to_chunk_provider_without_header(monkeypatch, include_usage):
    _mock_stream_client(
        monkeypatch, _openrouter_chunks("Together", include_usage), header=None
    )

    text, metadata = _collect_stream_result(
        llm_openai.stream(
            [Message(role="user", content="Say ok.")],
            "openrouter/meta-llama/llama-3.1",
            None,
        )
    )

    assert text == "ok"
    assert metadata is not None
    assert metadata["resolved_model"] == "openrouter/meta-llama/llama-3.1@together"
    if include_usage:
        assert metadata["usage"] == {"input_tokens": 3, "output_tokens": 2}


def test_stream_prefers_header_over_chunk_provider(monkeypatch):
    _mock_stream_client(
        monkeypatch, _openrouter_chunks("Together", True), header="Groq"
    )

    _, metadata = _collect_stream_result(
        llm_openai.stream(
            [Message(role="user", content="Say ok.")],
            "openrouter/meta-llama/llama-3.1",
            None,
        )
    )

    assert metadata is not None
    assert metadata["resolved_model"] == "openrouter/meta-llama/llama-3.1@groq"


def test_openrouter_provider_from_ignores_non_string_values():
    assert llm_openai._openrouter_provider_from(MagicMock()) is None
    assert llm_openai._openrouter_provider_from(SimpleNamespace(provider="  ")) is None
    assert (
        llm_openai._openrouter_provider_from(SimpleNamespace(provider=" Together "))
        == "Together"
    )


def test_stream_records_cumulative_usage_once(monkeypatch):
    """Providers like Gemini attach cumulative usage to every chunk; count one request."""

    def chunk(content, completion_tokens):
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=completion_tokens,
                total_tokens=10 + completion_tokens,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        reasoning=None,
                        content=content,
                        tool_calls=None,
                    ),
                )
            ],
        )

    chunks = [chunk("a", 1), chunk("b", 2), chunk("c", 3)]
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=chunks))
        )
    )
    record = Mock()
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "record_llm_request", record)

    text, metadata = _collect_stream_result(
        llm_openai.stream(
            [Message(role="user", content="Say abc.")], "openai/gpt-4o", None
        )
    )

    assert text == "abc"
    assert record.call_count == 1
    assert record.call_args.kwargs["output_tokens"] == 3
    assert metadata is not None
    assert metadata["usage"] == {"input_tokens": 10, "output_tokens": 3}


def test_stream_records_usage_when_closed_early(monkeypatch):
    """Closing the stream early must still record usage already received.

    Providers with cumulative per-chunk usage may deliver it before the user
    interrupts; the deferred post-loop recording must not be skipped when the
    consumer closes the generator.
    """

    def chunk(content, completion_tokens):
        return SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=completion_tokens,
                total_tokens=10 + completion_tokens,
            ),
            choices=[
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        reasoning=None,
                        content=content,
                        tool_calls=None,
                    ),
                )
            ],
        )

    chunks = [chunk("a", 1), chunk("b", 2)]
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=chunks))
        )
    )
    record = Mock()
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: mock_client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "record_llm_request", record)

    gen = llm_openai.stream(
        [Message(role="user", content="Say abc.")], "openai/gpt-4o", None
    )
    assert next(gen) == "a"
    gen.close()

    assert record.call_count == 1
    assert record.call_args.kwargs["output_tokens"] == 1
