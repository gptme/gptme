"""Anthropic streaming over a mocked SSE transport (real SDK parsing).

- Usage: the stream reports input_tokens / cache_*_input_tokens on
  message_start and usually only ``output_tokens`` on message_delta; omitted
  fields must keep their message_start values.
- Thinking: a clamped budget must not drop below the 1024 API minimum.
- A tool_use block with ``input: {}`` streams no argument JSON; the emitted
  call must still parse as a tool call.
"""

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx
import pytest

from gptme import llm
from gptme.llm import llm_anthropic
from gptme.message import Message
from gptme.tools.base import ToolSpec, ToolUse


def _sse(events):
    return "".join(
        f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n" for ev in events
    ).encode()


EVENTS = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 1200,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 5000,
                "output_tokens": 1,
            },
        },
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "Hello"},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        # Exactly the documented shape: only output_tokens.
        "usage": {"output_tokens": 15},
    },
    {"type": "message_stop"},
]


def test_stream_metadata_keeps_message_start_input_tokens(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(EVENTS),
        )

    client = anthropic.Anthropic(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)

    gen = llm_anthropic.stream(
        [Message("system", "sys"), Message("user", "hi")],
        "claude-sonnet-4-6",
        None,
    )
    parts = []
    while True:
        try:
            parts.append(next(gen))
        except StopIteration as exc:
            metadata = exc.value
            break

    assert "".join(parts) == "Hello"
    assert metadata is not None
    usage = metadata["usage"]
    assert usage["output_tokens"] == 15
    # Input/cache counts from message_start must not be lost.
    assert usage == {
        "input_tokens": 1200,
        "output_tokens": 15,
        "cache_read_tokens": 5000,
        "cache_creation_tokens": 200,
    }


def test_merge_stream_usage_prefers_delta_values_when_present():
    start = SimpleNamespace(
        input_tokens=10,
        output_tokens=1,
        cache_creation_input_tokens=2,
        cache_read_input_tokens=3,
    )
    delta = SimpleNamespace(
        input_tokens=12,
        output_tokens=40,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    merged = llm_anthropic._merge_stream_usage(start, delta)
    assert merged.input_tokens == 12
    assert merged.output_tokens == 40
    assert merged.cache_creation_input_tokens == 2
    assert merged.cache_read_input_tokens == 3
    assert llm_anthropic._merge_stream_usage(None, delta) is delta


NOARG_EVENTS: list[dict[str, Any]] = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-6",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        },
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "tool_use",
            "id": "toolu_01",
            "name": "complete",
            "input": {},
        },
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": ""},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "tool_use", "stop_sequence": None},
        "usage": {"output_tokens": 12},
    },
    {"type": "message_stop"},
]


@pytest.mark.parametrize("partial_json", ["", "{}"])
@pytest.mark.parametrize("stop_reason", ["tool_use", "end_turn", "refusal", None])
def test_streamed_tool_call_requires_tool_use_stop(
    monkeypatch, partial_json, stop_reason
):
    events = deepcopy(NOARG_EVENTS)
    events[2]["delta"]["partial_json"] = partial_json
    events[4]["delta"]["stop_reason"] = stop_reason

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(events),
        )

    client = anthropic.Anthropic(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)
    successes: list[bool] = []
    monkeypatch.setattr(
        llm_anthropic,
        "record_llm_request",
        lambda **kwargs: successes.append(kwargs["success"]),
    )

    gen = llm_anthropic.stream(
        [Message("system", "sys"), Message("user", "you are done")],
        "claude-sonnet-4-6",
        None,
    )
    parts: list[str] = []
    if stop_reason != "tool_use":
        error_type: type[httpx.RemoteProtocolError] | type[anthropic.APIError]
        if stop_reason is None:
            # A missing terminal reason is still an incomplete protocol stream.
            monkeypatch.setenv("GPTME_TEST_MAX_RETRIES", "1")
            error_type = httpx.RemoteProtocolError
        else:
            error_type = anthropic.APIError

            def unexpected_backoff(*args, **kwargs):
                pytest.fail("A terminal tool response must not be retried")

            monkeypatch.setattr(llm_anthropic, "backoff_wait", unexpected_backoff)
            monkeypatch.setattr(llm_anthropic, "get_max_retries", lambda: 3)
            monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
        with pytest.raises(error_type, match="stop_reason") as exc:
            parts.extend(gen)
        if stop_reason is not None:
            from gptme.llm import (
                is_llm_reply_error,
                is_provider_error,
                mark_llm_reply_origin,
            )
            from gptme.llm.recovery import is_transient_provider_error

            mark_llm_reply_origin(exc.value)
            assert is_provider_error(exc.value)
            assert is_llm_reply_error(exc.value)
            assert not is_transient_provider_error(exc.value)
        assert len(requests) == 1
        assert successes == [False]
        assert not list(ToolUse.iter_from_content("".join(parts), "tool"))
        return

    parts.extend(gen)
    text = "".join(parts)
    calls = list(ToolUse.iter_from_content(text, "tool"))
    assert [(c.tool, c.call_id, c.kwargs) for c in calls] == [
        ("complete", "toolu_01", {})
    ], repr(text)
    assert successes == [True]


def _tool_block(index, call_id, name, partial_json):
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        },
        {"type": "content_block_stop", "index": index},
    ]


def _stream_truncated(monkeypatch, blocks, stop_reason):
    """Stream ``blocks`` ending in a truncation; return text, requests, successes."""
    events = [
        NOARG_EVENTS[0],
        *blocks,
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    ]
    requests = []
    successes: list[bool] = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(events),
        )

    client = anthropic.Anthropic(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)
    monkeypatch.setattr(
        llm_anthropic,
        "record_llm_request",
        lambda **kwargs: successes.append(kwargs["success"]),
    )
    text = "".join(
        llm_anthropic.stream(
            [Message("system", "sys"), Message("user", "write files")],
            "claude-sonnet-4-6",
            None,
        )
    )
    return text, requests, successes


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
@pytest.mark.parametrize("cut_json", ["", "{}", '{"path": "big.txt", "content": "ab'])
def test_truncated_reply_keeps_finished_tool_calls(monkeypatch, stop_reason, cut_json):
    """Only the call being written at the cut is withheld; nothing is retried."""
    text, requests, successes = _stream_truncated(
        monkeypatch,
        [
            *_tool_block(0, "toolu_01", "shell", '{"command": "ls"}'),
            *_tool_block(1, "toolu_02", "save", cut_json),
        ],
        stop_reason,
    )

    calls = list(ToolUse.iter_from_content(text, "tool"))
    assert [(c.tool, c.call_id, c.kwargs) for c in calls] == [
        ("shell", "toolu_01", {"command": "ls"})
    ], repr(text)
    assert "toolu_02" not in text
    assert f"`save` tool call in this reply was cut off ({stop_reason})" in text
    assert len(requests) == 1
    assert successes == [True]


def test_truncated_text_after_tool_call_keeps_the_call(monkeypatch):
    """A cut in a later text block leaves the preceding tool call complete."""
    text, _, _ = _stream_truncated(
        monkeypatch,
        [
            *_tool_block(0, "toolu_01", "shell", '{"command": "ls"}'),
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Next I will"},
            },
            {"type": "content_block_stop", "index": 1},
        ],
        "max_tokens",
    )

    calls = list(ToolUse.iter_from_content(text, "tool"))
    assert [c.call_id for c in calls] == ["toolu_01"], repr(text)
    assert text.endswith("Next I will")
    assert "cut off" not in text


@pytest.mark.parametrize("thinking", [False, True])
@pytest.mark.parametrize("break_on_tooluse", [False, True])
def test_cutoff_notice_reaches_the_next_request(
    monkeypatch, break_on_tooluse, thinking
):
    """The model must learn a call was skipped, even after an early tool break."""
    text, _, _ = _stream_truncated(
        monkeypatch,
        [
            *_tool_block(0, "toolu_01", "shell", '{"command": "ls"}'),
            *_tool_block(1, "toolu_02", "save", '{"path": "big.txt", "content": "ab'),
        ],
        "max_tokens",
    )
    if thinking:
        text = f"<think>\nplan\n<!-- think-sig: sig== -->\n</think>\n\n{text}"
    monkeypatch.setattr(
        llm,
        "_stream",
        lambda *args, **kwargs: llm._StreamWithMetadata(
            (chunk for chunk in [text]), "anthropic/claude-sonnet-4-6"
        ),
    )
    monkeypatch.setattr(llm, "get_tool_format", lambda: "tool")
    reply = llm._reply_stream(
        [Message("user", "write files")],
        "anthropic/claude-sonnet-4-6",
        None,
        break_on_tooluse=break_on_tooluse,
    )

    shell = ToolSpec(name="shell", desc="run a command", parameters=[])
    sent, _, _ = llm_anthropic._prepare_messages_for_api(
        [
            Message("system", "sys"),
            Message("user", "write files"),
            reply,
            Message("system", "file.txt", call_id="toolu_01"),
        ],
        [shell],
    )

    blocks: list[Any] = list(sent[1]["content"])
    prose = "".join(b["text"] for b in blocks if b["type"] == "text")
    assert "`save` tool call in this reply was cut off (max_tokens)" in prose
    assert [b["id"] for b in blocks if b["type"] == "tool_use"] == ["toolu_01"]


@pytest.mark.parametrize(
    ("max_tokens", "expected"),
    [
        (300, None),
        (800, None),
        (1200, None),
        (1279, None),
        (1280, {"type": "enabled", "budget_tokens": 1024}),
    ],
)
def test_clamped_thinking_budget_respects_api_minimum(max_tokens, expected):
    """A clamped budget below Anthropic's 1024 minimum disables thinking."""
    model = "claude-sonnet-4-5"  # budget_tokens (non-adaptive) thinking
    budget, use_thinking = llm_anthropic._adjust_thinking_budget(
        max_tokens, 16000, True, model
    )
    param = llm_anthropic._build_thinking_param(model, use_thinking, budget)
    assert param == expected


@pytest.mark.parametrize("thinking_budget", [0, 500, 1023, 1024])
def test_configured_thinking_budget_respects_api_minimum(thinking_budget):
    model = "claude-sonnet-4-5"
    budget, use_thinking = llm_anthropic._adjust_thinking_budget(
        64000, thinking_budget, True, model
    )
    param = llm_anthropic._build_thinking_param(model, use_thinking, budget)
    expected = (
        {"type": "enabled", "budget_tokens": 1024} if thinking_budget == 1024 else None
    )
    assert param == expected


@pytest.mark.parametrize("missing_terminal", ["stop_reason", "message_stop"])
def test_incomplete_tool_stream_still_retries(monkeypatch, missing_terminal):
    incomplete = deepcopy(NOARG_EVENTS)
    if missing_terminal == "stop_reason":
        incomplete[4]["delta"]["stop_reason"] = None
    else:
        incomplete.pop()
    requests = []
    backoffs = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(incomplete if len(requests) == 1 else NOARG_EVENTS),
        )

    client = anthropic.Anthropic(
        api_key="sk-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)
    monkeypatch.setattr(llm_anthropic, "get_max_retries", lambda: 3)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(
        llm_anthropic, "backoff_wait", lambda *args: backoffs.append(args)
    )
    text = "".join(
        llm_anthropic.stream(
            [Message("system", "sys"), Message("user", "you are done")],
            "claude-sonnet-4-6",
            None,
        )
    )
    assert len(requests) == 2
    assert len(backoffs) == 1
    calls = list(ToolUse.iter_from_content(text, "tool"))
    assert [(c.tool, c.call_id, c.kwargs) for c in calls] == [
        ("complete", "toolu_01", {})
    ]
