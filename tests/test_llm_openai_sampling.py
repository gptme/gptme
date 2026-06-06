from types import SimpleNamespace
from unittest.mock import Mock

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
    completions_create = Mock(return_value=completion)
    mock_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=completions_create))
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
