"""Anthropic native system messages must respect turn and tool-result placement."""

from typing import Literal

import httpx2
import pytest
from anthropic import Anthropic
from pydantic import TypeAdapter
from typing_extensions import NotRequired, TypedDict

from gptme.llm import llm_anthropic
from gptme.message import Message
from gptme.prompts import SYSTEM_PROMPT_CACHE_BOUNDARY
from gptme.tools import get_tool, init_tools


class _RequestBlock(TypedDict):
    type: str
    id: NotRequired[str]
    tool_use_id: NotRequired[str]
    text: NotRequired[str]
    content: NotRequired[list["_RequestBlock"]]


class _RequestMessage(TypedDict):
    role: str
    content: list[_RequestBlock]


class _RequestPayload(TypedDict):
    messages: list[_RequestMessage]


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
    original = [(message.role, message.content) for message in messages]

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
    assert [(message.role, message.content) for message in messages] == original


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
    payloads: list[_RequestPayload] = []
    payload_parser = TypeAdapter(_RequestPayload)
    event_encoder = TypeAdapter(dict[str, object])

    def serve(request: httpx2.Request) -> httpx2.Response:
        payloads.append(payload_parser.validate_json(request.content))
        events: list[dict[str, object]] = [
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
            f"event: {event['type']}\ndata: {event_encoder.dump_json(event).decode()}\n\n"
            for event in events
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


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-opus-4-8"])
@pytest.mark.parametrize("notice_before_first_result", [False, True])
def test_notices_inside_tool_batch_follow_all_results(
    model: str, notice_before_first_result: bool
) -> None:
    """cwd hooks and interrupted/warning output cannot split a result batch."""
    init_tools(["shell"])
    shell = get_tool("shell")
    assert shell is not None
    assistant = Message(
        "assistant",
        '@shell(toolu_1): {"command": "cd /tmp"}\n'
        '@shell(toolu_2): {"command": "echo hi"}',
    )
    first = Message("system", "first result", call_id="toolu_1")
    notice = Message("system", "cwd or interrupted output", hide=True)
    turn = [notice, first] if notice_before_first_result else [first, notice]
    messages = [
        Message("system", "Bootstrap"),
        Message("user", "Run commands"),
        assistant,
        *turn,
        Message("system", "second result", call_id="toolu_2"),
        Message("user", "Continue"),
    ]
    original = [(m.role, m.content, m.call_id) for m in messages]

    prepared, _, _ = llm_anthropic._prepare_messages_for_api(messages, [shell], model)
    sent = TypeAdapter(list[_RequestMessage]).validate_python(prepared)

    assert [block["type"] for block in sent[1]["content"]] == ["tool_use", "tool_use"]
    assert sent[2]["content"][0]["type"] == "tool_result"
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert sent[3]["content"][0]["type"] == "tool_result"
    assert sent[3]["content"][0]["tool_use_id"] == "toolu_2"
    assert "cwd or interrupted output" in str(sent[4:])
    assert "Continue" in str(sent[4:])
    assert [(m.role, m.content, m.call_id) for m in messages] == original


@pytest.mark.parametrize(
    "notice", ["Command interrupted: partial output", "shellcheck warning"]
)
def test_notice_before_single_tool_result_follows_result(notice: str) -> None:
    init_tools(["shell"])
    shell = get_tool("shell")
    assert shell is not None
    messages = [
        Message("system", "Bootstrap"),
        Message("user", "Run command"),
        Message("assistant", '@shell(toolu_1): {"command": "echo hi"}'),
        Message("system", notice),
        Message("system", "Interrupted by user", call_id="toolu_1"),
    ]
    prepared, _, _ = llm_anthropic._prepare_messages_for_api(
        messages, [shell], "claude-sonnet-4-6"
    )
    sent = TypeAdapter(list[_RequestMessage]).validate_python(prepared)
    assert sent[2]["content"][0]["type"] == "tool_result"
    assert sent[2]["content"][0]["tool_use_id"] == "toolu_1"
    assert notice in str(sent[3:])


def test_buffered_notices_stay_with_their_tool_batch() -> None:
    """Buffering must neither lose a tail notice nor move it past another batch."""
    messages = [
        {"role": "user", "content": "Before tools"},
        {"role": "assistant", "content": '@shell(a): {"command": "pwd"}'},
        {"role": "user", "content": "notice A"},
        {"role": "user", "content": "result A", "call_id": "a"},
        {"role": "assistant", "content": '@shell(b): {"command": "pwd"}'},
        {"role": "user", "content": "notice B"},
        {"role": "user", "content": "result B", "call_id": "b"},
    ]
    sent = list(llm_anthropic._handle_tools(messages))
    assert sent[0] == messages[0]
    assert sent[2]["content"][0]["tool_use_id"] == "a"
    assert sent[3] == messages[2]
    assert sent[4]["content"][0]["id"] == "b"
    assert sent[5]["content"][0]["tool_use_id"] == "b"
    assert sent[6] == messages[5]
    assert messages[3]["call_id"] == "a"


def test_incomplete_batch_does_not_drop_notice() -> None:
    messages = [
        {"role": "assistant", "content": '@shell(a): {"command": "pwd"}'},
        {"role": "user", "content": "notice without result"},
        {"role": "assistant", "content": "Interrupted"},
        {"role": "user", "content": "Continue"},
    ]
    sent = list(llm_anthropic._handle_tools(messages))
    assert sent[1] == messages[1]
    assert sent[-1] == messages[-1]


def test_non_native_tools_do_not_reorder_system_notices() -> None:
    messages = [
        Message("system", "Bootstrap"),
        Message("user", "Run command"),
        Message("assistant", "```shell\necho hi\n```"),
        Message("system", "shellcheck warning"),
        Message("system", "hi"),
    ]
    sent, _, _ = llm_anthropic._prepare_messages_for_api(
        messages, None, "claude-sonnet-4-6"
    )
    assert "shellcheck warning" in str(sent[2])
    assert str(sent[2]).index("shellcheck warning") < str(sent[2]).index("hi")
