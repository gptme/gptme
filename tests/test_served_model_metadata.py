"""``MessageMetadata.served_model``: the model id the provider actually served.

Contract: the raw, unprefixed model string exactly as reported in the provider
response. Recorded whenever the response carries a non-empty model string —
including when it equals the requested model (presence = verified). Absent when
the provider did not report one. Independent of ``resolved_model``.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from gptme.llm import llm_anthropic, llm_openai, llm_openai_subscription
from gptme.llm.llm_openai import _record_usage
from gptme.llm.models import get_model
from gptme.llm.openai_responses import (
    _stream_responses_events,
    served_model_from,
)
from gptme.message import Message


def _drain(gen) -> tuple[str, Any]:
    parts: list[str] = []
    while True:
        try:
            parts.append(next(gen))
        except StopIteration as exc:
            return "".join(parts), exc.value


def _chat_usage():
    from openai.types.completion_usage import CompletionUsage

    return CompletionUsage.model_validate(
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )


def _responses_usage():
    from openai.types.responses.response_usage import ResponseUsage

    return ResponseUsage.model_validate(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    )


@pytest.fixture(autouse=True)
def _no_effort(monkeypatch):
    monkeypatch.delenv("GPTME_THINKING_EFFORT", raising=False)


# --- helper --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("obj", "expected"),
    [
        ({"model": "gpt-5.6-sol"}, "gpt-5.6-sol"),
        (SimpleNamespace(model="claude-sonnet-4-6"), "claude-sonnet-4-6"),
        ({"model": ""}, None),
        ({"model": "   "}, None),
        ({"model": None}, None),
        ({}, None),
        (SimpleNamespace(), None),
        (MagicMock(), None),  # non-string attribute (e.g. test mocks)
        (None, None),
    ],
)
def test_served_model_from(obj, expected):
    assert served_model_from(obj) == expected


# --- shared Responses event loop ------------------------------------------------


def test_responses_events_model_callback_prefers_completed():
    seen: list[str] = []
    events = [
        {"type": "response.created", "response": {"model": "gpt-5.6-sol-early"}},
        {"type": "response.in_progress", "response": {"model": "gpt-5.6-sol-mid"}},
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.completed", "response": {"model": "gpt-5.6-sol"}},
    ]
    text = "".join(_stream_responses_events(events, model_callback=seen.append))
    assert text == "hi"
    # Last non-empty value wins -> the completed event's model.
    assert seen[-1] == "gpt-5.6-sol"


def test_responses_events_model_from_created_when_completed_lacks_it():
    seen: list[str] = []
    events = [
        {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.done", "response": {"model": ""}},
    ]
    list(_stream_responses_events(events, model_callback=seen.append))
    assert seen == ["gpt-5.6-sol"]


def test_responses_events_model_fires_before_usage():
    order: list[str] = []
    events = [
        {
            "type": "response.completed",
            "response": {"model": "gpt-5", "usage": {"input_tokens": 1}},
        },
    ]
    list(
        _stream_responses_events(
            events,
            usage_callback=lambda u: order.append("usage"),
            model_callback=lambda m: order.append(f"model:{m}"),
        )
    )
    assert order == ["model:gpt-5", "usage"]


def test_responses_events_no_model_no_callback():
    seen: list[str] = []
    events = [
        {"type": "response.created", "response": {}},
        {"type": "response.output_text.delta", "delta": "hi"},
        {"type": "response.completed", "response": {"usage": None}},
    ]
    list(_stream_responses_events(events, model_callback=seen.append))
    assert seen == []


# --- openai-subscription --------------------------------------------------------


class _FakeSSE:
    def __init__(self, events: list[dict[str, Any]]):
        import json

        self.status_code = 200
        self._lines = [f"data: {json.dumps(e)}".encode() for e in events]

    def iter_lines(self):
        yield from self._lines

    def close(self):
        pass


def _drain_subscription(events: list[dict[str, Any]]) -> tuple[str, Any]:
    auth = llm_openai_subscription.SubscriptionAuth(
        access_token="x", refresh_token="y", account_id="acct", expires_at=1e12
    )
    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=auth),
        patch(
            "gptme.llm.llm_openai_subscription.requests.post",
            return_value=_FakeSSE(events),
        ),
    ):
        return _drain(
            llm_openai_subscription.stream(
                [Message(role="user", content="hello")], "gpt-5.6-sol"
            )
        )


def test_subscription_records_served_model_equal_to_requested():
    """Live shape: the ChatGPT backend echoes the requested model on every event."""
    _, metadata = _drain_subscription(
        [
            {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
            {"type": "response.in_progress", "response": {"model": "gpt-5.6-sol"}},
            {"type": "response.output_text.delta", "delta": "ok"},
            {
                "type": "response.completed",
                "response": {
                    "model": "gpt-5.6-sol",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            },
        ]
    )
    assert metadata["served_model"] == "gpt-5.6-sol"
    assert metadata["usage"]["input_tokens"] == 10


def test_subscription_served_model_on_effort_only_path():
    """No usage on response.done -> effort-only metadata still carries served_model."""
    _, metadata = _drain_subscription(
        [
            {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
            {"type": "response.done"},
        ]
    )
    assert metadata == {"reasoning_effort": "medium", "served_model": "gpt-5.6-sol"}


def test_subscription_missing_model_leaves_key_absent():
    _, metadata = _drain_subscription([{"type": "response.done"}])
    assert metadata == {"reasoning_effort": "medium"}


def test_subscription_non_streaming_chat_complete_keeps_served_model():
    """reply() non-streaming mode goes through _chat_complete -> chat_with_metadata;
    the stream's metadata (incl. served_model) must reach the message."""
    from gptme.llm import _chat_complete

    events: list[dict[str, Any]] = [
        {"type": "response.created", "response": {"model": "gpt-5.6-sol"}},
        {"type": "response.output_text.delta", "delta": "ok"},
        {
            "type": "response.completed",
            "response": {
                "model": "gpt-5.6-sol",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        },
    ]
    auth = llm_openai_subscription.SubscriptionAuth(
        access_token="x", refresh_token=None, account_id="acct", expires_at=1e12
    )
    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=auth),
        patch(
            "gptme.llm.llm_openai_subscription.requests.post",
            return_value=_FakeSSE(events),
        ),
    ):
        content, metadata = _chat_complete(
            [Message(role="user", content="hello")],
            "openai-subscription/gpt-5.6-sol",
            None,
        )
    assert content == "ok"
    assert metadata is not None
    assert metadata["model"] == "openai-subscription/gpt-5.6-sol"
    assert metadata["served_model"] == "gpt-5.6-sol"
    assert metadata["usage"]["output_tokens"] == 2


def test_subscription_chat_still_returns_plain_text():
    with patch.object(
        llm_openai_subscription,
        "stream",
        side_effect=lambda *a, **k: _gen_with_return(["o", "k"], {"x": 1}),
    ):
        assert (
            llm_openai_subscription.chat([Message(role="user", content="hi")], "m")
            == "ok"
        )
        assert llm_openai_subscription.chat_with_metadata(
            [Message(role="user", content="hi")], "m"
        ) == ("ok", {"x": 1})


def _gen_with_return(parts: list[str], value: Any):
    yield from parts
    return value


# --- OpenAI-compatible: _record_usage ----------------------------------------------


def test_record_usage_served_model_with_and_without_usage():
    with_usage = _record_usage(_chat_usage(), "openai/gpt-5", served_model="gpt-5")
    assert with_usage is not None
    assert with_usage["served_model"] == "gpt-5"
    assert with_usage["model"] == "openai/gpt-5"

    assert _record_usage(None, "openai/gpt-5", served_model="gpt-5-2026-08-01") == {
        "model": "openai/gpt-5",
        "served_model": "gpt-5-2026-08-01",
    }
    assert "served_model" not in (_record_usage(_chat_usage(), "openai/gpt-5") or {})
    assert _record_usage(None, "openai/gpt-5") is None


def test_record_usage_served_model_independent_of_resolved_model():
    metadata = _record_usage(
        None,
        "openrouter/deepseek/deepseek-v4-flash",
        resolved_model="openrouter/deepseek/deepseek-v4-flash@together",
        served_model="deepseek/deepseek-v4-flash-0731",
    )
    assert metadata == {
        "model": "openrouter/deepseek/deepseek-v4-flash",
        "resolved_model": "openrouter/deepseek/deepseek-v4-flash@together",
        "served_model": "deepseek/deepseek-v4-flash-0731",
    }


# --- OpenAI-compatible: chat() --------------------------------------------------


def _completion(model: Any, usage: Any = None):
    ns = SimpleNamespace(
        usage=usage,
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="ok", tool_calls=None),
            )
        ],
    )
    if model is not None:
        ns.model = model
    return ns


def _mock_chat_client(monkeypatch, completion):
    raw_resp = SimpleNamespace(parse=lambda: completion, headers={})
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                with_raw_response=SimpleNamespace(create=Mock(return_value=raw_resp))
            )
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)


def test_chat_completions_records_served_model(monkeypatch):
    _mock_chat_client(monkeypatch, _completion("gpt-4o-2024-08-06", _chat_usage()))
    _, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-4o", None
    )
    assert metadata is not None
    assert metadata["served_model"] == "gpt-4o-2024-08-06"
    assert metadata["usage"]["input_tokens"] == 100


def test_chat_completions_served_equal_to_requested_without_usage(monkeypatch):
    _mock_chat_client(monkeypatch, _completion("gpt-4o", None))
    _, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-4o", None
    )
    assert metadata == {"model": "openai/gpt-4o", "served_model": "gpt-4o"}


def test_chat_completions_missing_model_key_absent(monkeypatch):
    _mock_chat_client(monkeypatch, _completion(None, _chat_usage()))
    _, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-4o", None
    )
    assert metadata is not None
    assert "served_model" not in metadata


def test_chat_responses_api_records_served_model(monkeypatch):
    response = SimpleNamespace(
        model="gpt-5-2026-08-07",
        usage=_responses_usage(),
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text="ok")],
            )
        ],
    )
    client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock(return_value=response))
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: True)

    _, metadata = llm_openai.chat(
        [Message(role="user", content="hi")], "openai/gpt-5", None
    )
    assert metadata is not None
    assert metadata["served_model"] == "gpt-5-2026-08-07"


# --- OpenAI-compatible: stream() ------------------------------------------------


def _chunk(content: str | None, model: Any = None, usage: Any = None):
    ns = SimpleNamespace(
        usage=usage,
        choices=(
            [
                SimpleNamespace(
                    finish_reason=None,
                    delta=SimpleNamespace(
                        reasoning_content=None,
                        reasoning=None,
                        content=content,
                        tool_calls=None,
                    ),
                )
            ]
            if content is not None
            else []
        ),
    )
    if model is not None:
        ns.model = model
    return ns


def _mock_stream_client(monkeypatch, chunks):
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(return_value=chunks))
        )
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)


def test_stream_completions_records_served_model_with_usage(monkeypatch):
    _mock_stream_client(
        monkeypatch,
        [
            _chunk("o", model="gpt-4o-2024-08-06"),
            _chunk("k", model="gpt-4o-2024-08-06"),
            _chunk(None, model="gpt-4o-2024-08-06", usage=_chat_usage()),
        ],
    )
    text, metadata = _drain(
        llm_openai.stream([Message(role="user", content="hi")], "openai/gpt-4o", None)
    )
    assert text == "ok"
    assert metadata["served_model"] == "gpt-4o-2024-08-06"
    assert metadata["usage"]["output_tokens"] == 50


def test_stream_completions_served_model_without_usage_chunk(monkeypatch):
    _mock_stream_client(monkeypatch, [_chunk("ok", model="gpt-4o")])
    _, metadata = _drain(
        llm_openai.stream([Message(role="user", content="hi")], "openai/gpt-4o", None)
    )
    assert metadata == {"model": "openai/gpt-4o", "served_model": "gpt-4o"}


def test_stream_completions_usage_chunk_without_model_keeps_earlier(monkeypatch):
    """A usage chunk lacking ``model`` must not erase the earlier served value."""
    _mock_stream_client(
        monkeypatch,
        [_chunk("ok", model="gpt-4o"), _chunk(None, usage=_chat_usage())],
    )
    _, metadata = _drain(
        llm_openai.stream([Message(role="user", content="hi")], "openai/gpt-4o", None)
    )
    assert metadata["served_model"] == "gpt-4o"


def test_stream_completions_missing_model_key_absent(monkeypatch):
    _mock_stream_client(monkeypatch, [_chunk("ok"), _chunk(None, usage=_chat_usage())])
    _, metadata = _drain(
        llm_openai.stream([Message(role="user", content="hi")], "openai/gpt-4o", None)
    )
    assert metadata is not None
    assert "served_model" not in metadata


def test_stream_responses_records_served_model(monkeypatch):
    events = [
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(model="gpt-5")
        ),
        SimpleNamespace(type="response.output_text.delta", delta="ok"),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(model="gpt-5", usage=_responses_usage()),
        ),
    ]
    client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock(return_value=events))
    )
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
    assert metadata["served_model"] == "gpt-5"
    assert metadata["usage"]["input_tokens"] == 100


def test_stream_responses_served_model_without_usage(monkeypatch):
    events = [
        SimpleNamespace(
            type="response.created", response=SimpleNamespace(model="gpt-5")
        ),
        SimpleNamespace(type="response.output_text.delta", delta="ok"),
        SimpleNamespace(type="response.completed", response=SimpleNamespace()),
    ]
    client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock(return_value=events))
    )
    monkeypatch.setattr(llm_openai, "get_client", lambda provider: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda client: False)

    _, metadata = _drain(
        llm_openai._stream_responses(
            [Message(role="user", content="hi")],
            "openai/gpt-5",
            None,
            get_model("openai/gpt-5"),
        )
    )
    assert metadata == {"model": "openai/gpt-5", "served_model": "gpt-5"}


# --- Anthropic ------------------------------------------------------------------

_ANTHROPIC_MSGS = [Message("system", "You are helpful."), Message("user", "hi")]


def _anthropic_response(model: Any):
    from anthropic.types import TextBlock

    ns = SimpleNamespace(
        content=[TextBlock(type="text", text="ok")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
    )
    if model is not None:
        ns.model = model
    return ns


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),  # equal to requested
        ("claude-sonnet-4-6-20250929", "claude-sonnet-4-6-20250929"),
        (None, None),
    ],
)
def test_anthropic_chat_served_model(monkeypatch, reported, expected):
    client = MagicMock()
    client.messages.create.return_value = _anthropic_response(reported)
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)

    _, metadata = llm_anthropic.chat(_ANTHROPIC_MSGS, "claude-sonnet-4-6", None)
    assert metadata is not None
    if expected is None:
        assert "served_model" not in metadata
    else:
        assert metadata["served_model"] == expected
        assert metadata["usage"]["output_tokens"] == 5


def _anthropic_stream_events(model: Any):
    from anthropic.types import TextDelta

    message = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
    )
    if model is not None:
        message.model = model
    return [
        SimpleNamespace(type="message_start", message=message),
        SimpleNamespace(
            type="content_block_delta",
            delta=TextDelta(type="text_delta", text="ok"),
        ),
        SimpleNamespace(
            type="message_delta",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ),
        ),
        SimpleNamespace(type="message_stop"),
    ]


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-sonnet-4-6-20250929", "claude-sonnet-4-6-20250929"),
        (None, None),
    ],
)
def test_anthropic_stream_served_model_from_message_start(
    monkeypatch, reported, expected
):
    client = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = _anthropic_stream_events(reported)
    client.messages.stream.return_value = cm
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)

    partial: dict = {}
    text, metadata = _drain(
        llm_anthropic.stream(
            _ANTHROPIC_MSGS,
            "claude-sonnet-4-6",
            None,
            _partial=partial,
        )
    )
    assert text == "ok"
    assert metadata is not None
    assert metadata["usage"]["output_tokens"] == 5
    if expected is None:
        assert "served_model" not in metadata
        assert "served_model" not in partial["metadata"]
    else:
        assert metadata["served_model"] == expected
        # break_on_tooluse fallback (message_start partial) carries it too.
        assert partial["metadata"]["served_model"] == expected


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "response.failed",
            "response": {"error": {"code": "server_error", "message": "boom"}},
        },
        {"type": "error", "code": "server_error", "message": "boom"},
    ],
)
def test_responses_stream_failure_raises_provider_error(event: dict) -> None:
    """A failed response must raise, not end the stream as an empty reply."""
    import openai

    events = [{"type": "response.output_text.delta", "delta": "partial"}, event]
    with pytest.raises(openai.APIError) as exc_info:
        list(_stream_responses_events(events))
    assert exc_info.value.body == {"code": "server_error", "message": "boom"}
    assert "boom" in str(exc_info.value)


def test_responses_stream_incomplete_warns_and_records_usage(caplog) -> None:
    usage: list = []
    events = [
        {"type": "response.output_text.delta", "delta": "truncated"},
        {
            "type": "response.incomplete",
            "response": {
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        },
    ]
    with caplog.at_level("WARNING", logger="gptme.llm.openai_responses"):
        text = "".join(_stream_responses_events(events, usage_callback=usage.append))
    assert text == "truncated"
    assert usage == [{"input_tokens": 1, "output_tokens": 2}]
    assert "max_output_tokens" in caplog.text


def test_responses_stream_incomplete_signals_before_usage() -> None:
    """Callers learn the response was incomplete before they record its usage."""
    order: list[str] = []
    events = [
        {
            "type": event_type,
            "response": {"usage": {"input_tokens": 1, "output_tokens": 2}},
        }
        for event_type in ("response.completed", "response.incomplete")
    ]
    for event in events:
        list(
            _stream_responses_events(
                [event],
                usage_callback=lambda u: order.append("usage"),
                incomplete_callback=lambda: order.append("incomplete"),
            )
        )
    assert order == ["usage", "incomplete", "usage"]


@pytest.mark.parametrize("success", [True, False])
def test_record_usage_passes_success_to_telemetry(success: bool) -> None:
    """An incomplete response keeps its usage/cost but is not a success."""
    with patch.object(llm_openai, "record_llm_request") as record:
        meta = _record_usage(
            {"input_tokens": 1, "output_tokens": 2}, "openai/gpt-5", success=success
        )
    assert record.call_args.kwargs["success"] is success
    assert meta is not None and meta.get("usage") is not None
