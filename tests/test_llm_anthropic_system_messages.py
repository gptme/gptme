"""Anthropic native system messages must respect turn and tool-result placement."""

import json
from typing import Literal

import httpx2
import pytest
from anthropic import Anthropic

from gptme.llm import llm_anthropic
from gptme.message import Message
from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY
from gptme.tools import get_tool, init_tools


@pytest.mark.parametrize(
    "model",
    [
        None,
        "claude-sonnet-4-6",
        "claude-opus-4-8",
        "anthropic/claude-opus-4-8-20260901",
        "openrouter/anthropic/claude-opus-4-8",
    ],
)
@pytest.mark.parametrize(
    ("roles", "native_allowed"),
    [
        (["user", "system"], True),
        (["user", "system", "assistant"], True),
        (["assistant", "system"], False),
        (["user", "system", "user"], False),
        (["user", "system", "system", "assistant"], True),
        (["user", "system", "system", "user"], False),
    ],
)
def test_native_system_message_placement(
    model: str | None,
    roles: list[Literal["system", "user", "assistant"]],
    native_allowed: bool,
) -> None:
    messages = [Message("system", "Bootstrap")]
    messages.extend(Message(role, f"message {i}") for i, role in enumerate(roles))
    original = [message.to_dict() for message in messages]

    transformed, system = llm_anthropic._transform_system_messages(messages, model)

    assert system == [{"type": "text", "text": "Bootstrap"}]
    keep_native = native_allowed and model is not None and "opus-4-8" in model
    assert sum(message.role == "system" for message in transformed) == (
        roles.count("system") if keep_native else 0
    )
    for i, role in enumerate(roles):
        if role == "system":
            text = f"message {i}"
            expected = text if keep_native else f"<system>{text}</system>"
            assert any(expected in message.content for message in transformed)
    assert [message.to_dict() for message in messages] == original


def test_native_system_context_boundary_before_first_user() -> None:
    messages = [
        Message("system", "Bootstrap"),
        Message("system", SYSTEM_PROMPT_CACHE_BOUNDARY),
        Message("system", "Dynamic context"),
        Message("user", "Hello"),
    ]

    transformed, _ = llm_anthropic._transform_system_messages(
        messages, "claude-opus-4-8"
    )

    assert [message.role for message in transformed] == ["user"]
    assert "<system>Dynamic context</system>" in transformed[0].content


@pytest.mark.parametrize("call_id", [None, "toolu_shell"])
def test_system_fallback_preserves_message_metadata(call_id: str | None) -> None:
    message = Message(
        "system",
        "Result or notice",
        call_id=call_id,
        ephemeral_ttl=2,
        hide=True,
        metadata={"model": "mock/test"},
    )
    transformed, _ = llm_anthropic._transform_system_messages(
        [Message("system", "Bootstrap"), Message("assistant", "Working"), message],
        "claude-opus-4-8",
    )

    assert transformed[-1].role == "user"
    assert transformed[-1].call_id == call_id
    assert transformed[-1].ephemeral_ttl == 2
    assert transformed[-1].hide is True
    assert transformed[-1].metadata == message.metadata
    assert message.role == "system"


@pytest.mark.parametrize("native_tools", [False, True])
@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-8"])
def test_stream_tool_results_use_user_turn(
    native_tools: bool, model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real SDK receives tool results before any subsequent harness notice."""
    init_tools(["shell"])
    shell = get_tool("shell")
    assert shell is not None
    call_id = "toolu_shell" if native_tools else None
    assistant = (
        '@shell(toolu_shell): {"command": "ls"}'
        if native_tools
        else "```shell\nls\n```"
    )
    messages = [
        Message("system", "Bootstrap"),
        Message("user", "List files"),
        Message("assistant", assistant),
        Message("system", "README.md", call_id=call_id),
        Message("system", "Continue carefully"),
    ]
    payloads: list[dict] = []

    def serve(request: httpx2.Request) -> httpx2.Response:
        payloads.append(json.loads(request.content))
        events: list[dict] = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_result",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 0},
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
                "delta": {"type": "text_delta", "text": "Done"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        sse = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
        )
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    with httpx2.Client(transport=httpx2.MockTransport(serve)) as http_client:
        client = Anthropic(api_key="test-key", http_client=http_client)
        monkeypatch.setattr(llm_anthropic, "_anthropic", client)
        assert (
            "".join(
                llm_anthropic.stream(messages, model, [shell] if native_tools else None)
            )
            == "Done"
        )

    sent = payloads[0]["messages"]
    assert [message["role"] for message in sent[:3]] == ["user", "assistant", "user"]
    if native_tools:
        assert any(
            block["type"] == "tool_use" and block["id"] == call_id
            for block in sent[1]["content"]
        )
        result = sent[2]["content"][0]
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == call_id
        assert result["content"][0]["text"] == "README.md"
        if model == "claude-opus-4-8":
            assert sent[3]["role"] == "system"
            assert sent[3]["content"][0]["text"] == "Continue carefully"
    else:
        assert "<system>README.md</system>" in sent[2]["content"][0]["text"]
