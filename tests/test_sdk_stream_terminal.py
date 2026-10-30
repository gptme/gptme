"""A successful HTTP EOF is not a successful SDK generation without its terminal."""

from collections.abc import Generator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import httpx2
import pytest
from anthropic.types import InputJSONDelta, TextDelta, ToolUseBlock
from openai.types.chat import ChatCompletionChunk

from gptme.llm import llm_anthropic, llm_openai
from gptme.llm.openai_responses import ResponsesStreamError, _stream_responses_events
from gptme.message import Message, MessageMetadata


def _chat_chunk(
    *, text: str | None = None, arguments: str | None = None, finish: str | None = None
) -> ChatCompletionChunk:
    delta: dict[str, Any] = {"content": text}
    if arguments is not None:
        delta["tool_calls"] = [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "shell", "arguments": arguments},
            }
        ]
    return ChatCompletionChunk.model_validate(
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-4o",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def _sdk_stream(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    *,
    terminal: bool = False,
    native: bool = False,
    empty: bool = False,
) -> Generator[str, None, MessageMetadata | None]:
    events: list[Any]
    if provider == "openai":
        events = (
            []
            if empty
            else [
                _chat_chunk(arguments='{"command":"ls"}')
                if native
                else _chat_chunk(text="hello")
            ]
        )
        if terminal:
            events += [_chat_chunk(finish="stop"), _chat_chunk()]
        client = MagicMock()
        client.chat.completions.create.return_value = iter(events)
        monkeypatch.setattr(llm_openai, "get_client", lambda _: client)
        monkeypatch.setattr(llm_openai, "_is_proxy", lambda _: False)
        monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)
        return llm_openai.stream.__wrapped__(
            [Message("user", "hi")], "openai/gpt-4o", None
        )
    events = []
    if not empty:
        if native:
            events.append(
                SimpleNamespace(
                    type="content_block_start",
                    content_block=ToolUseBlock(
                        type="tool_use", id="call_1", name="shell", input={}
                    ),
                )
            )
            events.append(
                SimpleNamespace(
                    type="content_block_delta",
                    delta=InputJSONDelta(
                        type="input_json_delta", partial_json='{"command":"ls"}'
                    ),
                )
            )
        else:
            events.append(
                SimpleNamespace(
                    type="content_block_delta",
                    delta=TextDelta(type="text_delta", text="hello"),
                )
            )
    if terminal:
        events.append(SimpleNamespace(type="message_stop"))
    client = MagicMock()
    client.messages.stream.return_value = nullcontext(iter(events))
    monkeypatch.setattr(llm_anthropic, "_anthropic", client)
    return llm_anthropic.stream.__wrapped__(
        [Message("system", "You are helpful."), Message("user", "hi")],
        "claude-sonnet-4-6",
        None,
    )


@pytest.mark.parametrize(
    ("provider", "error"),
    [("openai", httpx.RemoteProtocolError), ("anthropic", httpx2.RemoteProtocolError)],
)
@pytest.mark.parametrize("kind", ["text", "native", "empty"])
def test_sdk_clean_eof_without_terminal_is_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    error: type[Exception],
    kind: str,
) -> None:
    stream = _sdk_stream(
        monkeypatch, provider, native=kind == "native", empty=kind == "empty"
    )
    emitted: list[str] = []
    with pytest.raises(error, match="terminal|finish_reason|message_stop"):
        emitted.extend(stream)
    if kind == "native":
        assert "@shell" not in "".join(emitted)
        assert '"command"' not in "".join(emitted)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("native", [False, True])
def test_sdk_terminal_accepts_text_and_tools(
    monkeypatch: pytest.MonkeyPatch, provider: str, native: bool
) -> None:
    output = "".join(_sdk_stream(monkeypatch, provider, terminal=True, native=native))
    assert ('@shell(call_1): {"command":"ls"}' if native else "hello") in output


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_sdk_consumer_close_is_not_missing_terminal(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    stream = _sdk_stream(monkeypatch, provider)
    assert next(stream) == "hello"
    stream.close()


def _response_events(arguments: str = '{"command":"ls"}') -> list[dict[str, Any]]:
    return [
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "item_1",
                "call_id": "call_1",
                "name": "shell",
                "arguments": "",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": arguments,
        },
    ]


@pytest.mark.parametrize(
    "events",
    [
        [],
        [{"type": "response.output_text.delta", "delta": "hello"}],
        _response_events(),
        _response_events('{"command":'),
    ],
)
def test_responses_clean_eof_never_reconciles_native_arguments(
    events: list[dict[str, Any]],
) -> None:
    emitted: list[str] = []
    with pytest.raises(httpx.RemoteProtocolError, match="terminal|response.completed"):
        emitted.extend(_stream_responses_events(events))
    assert "@shell" not in "".join(emitted)


@pytest.mark.parametrize("terminal", ["response.completed", "response.done"])
def test_responses_terminal_allows_native_reconciliation(terminal: str) -> None:
    output = "".join(
        _stream_responses_events(_response_events() + [{"type": terminal}])
    )
    assert '@shell(call_1): {"command":"ls"}' in output


def test_responses_close_does_not_reconcile_or_raise() -> None:
    stream = _stream_responses_events(
        [{"type": "response.output_text.delta", "delta": "hello"}] + _response_events()
    )
    assert next(stream) == "hello"
    stream.close()


def test_responses_explicit_failure_preserves_error_code() -> None:
    with pytest.raises(ResponsesStreamError) as exc:
        list(
            _stream_responses_events(
                _response_events()
                + [
                    {
                        "type": "response.failed",
                        "response": {
                            "error": {
                                "code": "invalid_request_error",
                                "message": "invalid",
                            }
                        },
                    }
                ]
            )
        )
    assert exc.value.code == "invalid_request_error"


def test_responses_explicit_incomplete_keeps_existing_behavior() -> None:
    seen: list[bool] = []
    assert (
        list(
            _stream_responses_events(
                [{"type": "response.incomplete"}],
                incomplete_callback=lambda: seen.append(True),
            )
        )
        == []
    )
    assert seen == [True]
