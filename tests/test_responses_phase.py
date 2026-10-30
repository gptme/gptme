"""Offline output -> persistence -> next-request phase regressions (#4616)."""

import json
from collections.abc import Generator, Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from openai.types.responses import ResponseOutputMessage

from gptme.llm import llm_openai, llm_openai_subscription
from gptme.llm.models import get_model
from gptme.llm.openai_responses import _messages_to_responses_input
from gptme.logmanager.manager import _merge_consecutive_messages
from gptme.message import Message, MessageMetadata, msgs2dicts
from gptme.tools import get_tool


def _message(text: str, phase: str | None, index: int = 0) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "id": f"msg_{index}",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }
    if phase is not None:
        item["phase"] = phase
    return item


def _drain(gen: Generator[str, None, MessageMetadata | None]) -> Message:
    parts: list[str] = []
    while True:
        try:
            parts.append(next(gen))
        except StopIteration as done:
            return Message("assistant", "".join(parts), metadata=done.value)


def _reload(msg: Message, serialization: str) -> Message:
    if serialization == "json":
        data = json.loads(json.dumps(msg.to_dict()))
        return Message(data["role"], data["content"], metadata=data.get("metadata"))
    return Message.from_toml(msg.to_toml())


class _SSE:
    status_code = 200
    text = ""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def iter_lines(self) -> Iterator[bytes]:
        for event in self.events:
            yield f"data: {json.dumps(event)}".encode()

    def close(self) -> None:
        pass


def _generate(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    items: list[dict[str, Any]],
    source: str,
) -> Message:
    events: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if source == "terminal":
            continue
        added = dict(item, content=[]) if item["type"] == "message" else item
        events.append(
            {"type": "response.output_item.added", "output_index": index, "item": added}
        )
        if item["type"] == "message":
            for content_index, part in enumerate(item["content"]):
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "output_index": index,
                        "item_id": item["id"],
                        "content_index": content_index,
                        "delta": part["text"],
                    }
                )
        else:
            events.append(
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": index,
                    "delta": item["arguments"],
                }
            )
        events.append(
            {"type": "response.output_item.done", "output_index": index, "item": item}
        )
    events.append(
        {
            "type": "response.completed",
            "response": {
                "output": items if source in ("terminal", "both") else [],
                "usage": None,
            },
        }
    )
    if provider == "subscription":
        monkeypatch.setattr(
            llm_openai_subscription,
            "get_auth",
            lambda: SimpleNamespace(access_token="test", account_id="test"),
        )
        monkeypatch.setattr(
            llm_openai_subscription._http_session,
            "post",
            Mock(return_value=_SSE(events)),
        )
        return _drain(llm_openai_subscription.stream([Message("user", "hi")], "gpt-5"))

    # SDK models (including the installed SDK's forward-compatible phase field).
    def sdk_item(item: dict[str, Any]) -> Any:
        return (
            ResponseOutputMessage.model_construct(**item)
            if item["type"] == "message"
            else SimpleNamespace(**item)
        )

    sdk_events = [
        SimpleNamespace(
            **{
                **e,
                **({"item": sdk_item(e["item"])} if "item" in e else {}),
                **(
                    {
                        "response": SimpleNamespace(
                            output=[sdk_item(i) for i in e["response"]["output"]],
                            usage=None,
                        )
                    }
                    if "response" in e
                    else {}
                ),
            }
        )
        for e in events
    ]
    create = Mock(return_value=sdk_events)
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(llm_openai, "get_client", lambda _: client)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda _: False)
    if provider == "sdk_nonstream":
        create.return_value = SimpleNamespace(
            output=[sdk_item(i) for i in items], usage=None
        )
        text, metadata = llm_openai.chat([Message("user", "hi")], "openai/gpt-5", None)
        return Message("assistant", text, metadata=metadata)
    return _drain(
        llm_openai._stream_responses(
            [Message("user", "hi")], "openai/gpt-5", None, get_model("openai/gpt-5")
        )
    )


def _prepare(msg: Message, provider: str) -> list[dict[str, Any]]:
    messages = [msg, Message("system", "ok", call_id="call_1")]
    if provider == "subscription":
        return _messages_to_responses_input(messages)[1]
    shell = get_tool("shell")
    assert shell is not None
    return llm_openai._prepare_messages_for_responses_api(
        messages, "openai/gpt-5", [shell]
    )[1]


@pytest.mark.parametrize("provider", ["subscription", "sdk", "sdk_nonstream"])
@pytest.mark.parametrize("source", ["terminal", "deltas", "both"])
@pytest.mark.parametrize("serialization", ["json", "toml"])
def test_ordered_phase_roundtrip(
    monkeypatch: pytest.MonkeyPatch, provider: str, source: str, serialization: str
) -> None:
    items = [
        _message("I will inspect.", "commentary"),
        {
            "type": "function_call",
            "name": "shell",
            "call_id": "call_1",
            "arguments": '{"command":"pwd"}',
        },
        _message("Done.", "final_answer", 2),
    ]
    msg = _reload(_generate(monkeypatch, provider, items, source), serialization)
    replay = _prepare(msg, provider)
    assert [(i.get("type", "message"), i.get("phase")) for i in replay] == [
        ("message", "commentary"),
        ("function_call", None),
        ("message", "final_answer"),
        ("function_call_output", None),
    ]
    assert replay[0]["content"] == "I will inspect."
    assert replay[2]["content"] == "Done."
    assert json.loads(replay[1]["arguments"]) == {"command": "pwd"}
    assert replay[3]["call_id"] == "call_1"


@pytest.mark.parametrize(
    "phase", ["commentary", "final_answer", "partial_answer", "future_phase", None]
)
@pytest.mark.parametrize("provider", ["subscription", "sdk", "sdk_nonstream"])
def test_optional_phase_not_inferred(
    monkeypatch: pytest.MonkeyPatch, phase: str | None, provider: str
) -> None:
    msg = _generate(monkeypatch, provider, [_message("Hello", phase)], "terminal")
    replay = _prepare(msg, provider)[0]
    assert replay.get("phase") == phase
    if phase is None:
        assert "phase" not in replay
        assert not (msg.metadata or {}).get("responses_output")


def test_edits_and_reasoning_strip(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = _generate(
        monkeypatch, "subscription", [_message("Original", "commentary")], "terminal"
    )
    edited = msg.replace(content="Edited")
    assert _prepare(edited, "sdk")[0] == {"role": "assistant", "content": "Edited"}
    # Only reasoning present during capture may be stripped. Neither adding
    # reasoning nor changing original whitespace is an authorized content edit.
    from gptme.llm.responses_phase import ResponsesPhaseCapture

    capture = ResponsesPhaseCapture()
    capture.output_item(0, _message("Original", "commentary"))
    content = "<think>private reasoning</think>\nOriginal"
    with_reasoning = Message(
        "assistant", content, metadata=capture.metadata(None, content)
    )
    assert _prepare(with_reasoning, "sdk")[0]["phase"] == "commentary"
    assert (
        _prepare(with_reasoning.replace(content="Original"), "sdk")[0]["phase"]
        == "commentary"
    )
    assert "private reasoning" not in json.dumps(with_reasoning.metadata)
    assert "phase" not in _prepare(msg.replace(content=" Original "), "sdk")[0]
    assert "responses_output" not in msgs2dicts([msg])[0]
    chat_dicts, _ = llm_openai._prepare_messages_for_api([msg], "openai/gpt-4o", None)
    assert "phase" not in json.dumps(chat_dicts)
    assert "responses_output" not in json.dumps(chat_dicts)


def test_adjacent_phased_messages_survive_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _generate(
        monkeypatch, "subscription", [_message("First", "commentary")], "terminal"
    )
    second = _generate(
        monkeypatch, "subscription", [_message("Second", "final_answer")], "terminal"
    )
    assert len(_merge_consecutive_messages([first, second])) == 2
    items = _messages_to_responses_input(_merge_consecutive_messages([first, second]))[
        1
    ]
    assert [i["phase"] for i in items] == ["commentary", "final_answer"]


@pytest.mark.parametrize("provider", ["subscription", "sdk", "sdk_nonstream"])
@pytest.mark.parametrize("source", ["terminal", "deltas", "both"])
@pytest.mark.parametrize("text", ["  Héj 🌍\n\n", "\n\nHello\n", " \t "])
def test_whitespace_and_unicode(
    monkeypatch: pytest.MonkeyPatch, provider: str, source: str, text: str
) -> None:
    msg = _reload(
        _generate(monkeypatch, provider, [_message(text, "commentary")], source), "toml"
    )
    assert _prepare(msg, provider)[0] == {
        "role": "assistant",
        "content": text,
        "phase": "commentary",
    }


@pytest.mark.parametrize("provider", ["subscription", "sdk", "sdk_nonstream"])
@pytest.mark.parametrize("source", ["terminal", "deltas", "both"])
def test_multiple_content_parts(
    monkeypatch: pytest.MonkeyPatch, provider: str, source: str
) -> None:
    item = _message("Hello ", "final_answer")
    item["content"].append({"type": "output_text", "text": "🌍!", "annotations": []})
    msg = _generate(monkeypatch, provider, [item], source)
    replay = _prepare(msg, provider)
    assert replay[0] == {
        "role": "assistant",
        "content": "Hello 🌍!",
        "phase": "final_answer",
    }


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        1,
        [],
        "bad",
        {},
        {"items": None},
        {"items": [None]},
        {"items": [{"type": "message", "text": 4}]},
    ],
)
def test_malformed_metadata_falls_back(malformed: Any) -> None:
    from gptme.llm.responses_phase import _digest

    if isinstance(malformed, dict):
        malformed = {**malformed, "content_digest": _digest("Current")}
    msg = Message("assistant", "Current", metadata={"responses_output": malformed})
    assert _prepare(msg, "subscription")[0] == {
        "role": "assistant",
        "content": "Current",
    }
    assert _prepare(msg, "sdk")[0] == {"role": "assistant", "content": "Current"}


def test_buffered_tool_output_keeps_original_item_order() -> None:
    from gptme.llm.responses_phase import ResponsesPhaseCapture

    capture = ResponsesPhaseCapture()
    capture.output_item(0, _message("Before", "commentary"))
    capture.output_item(1, {"type": "function_call", "call_id": "call_1"})
    capture.output_item(2, _message("After", "final_answer", 2))
    # #4567 buffers function output until authoritative completion. Its display
    # order is not the wire output_index order. No parser changes in this PR.
    content = 'BeforeAfter\n@shell(call_1): {"command":"pwd"}'
    msg = Message("assistant", content, metadata=capture.metadata(None, content))
    replay = _prepare(msg, "sdk")
    assert [i.get("phase", i.get("type")) for i in replay] == [
        "commentary",
        "function_call",
        "final_answer",
        "function_call_output",
    ]


def test_rejected_call_not_reinstated_and_no_opaque_data() -> None:
    from gptme.llm.responses_phase import ResponsesPhaseCapture

    capture = ResponsesPhaseCapture()
    events = [
        {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "DO NOT STORE",
                        "summary": [{"text": "private"}],
                    },
                    _message("Hello", "commentary"),
                    {
                        "type": "function_call",
                        "call_id": "rejected",
                        "arguments": '{"command":"do not execute"}',
                    },
                ]
            },
        }
    ]
    list(capture.events(events))
    msg = Message("assistant", "Hello", metadata=capture.metadata(None, "Hello"))
    assert _prepare(msg, "subscription") == [
        {"role": "assistant", "content": "Hello", "phase": "commentary"}
    ]
    assert "private" not in json.dumps(msg.metadata)
    assert "DO NOT STORE" not in json.dumps(msg.metadata)
    assert "arguments" not in json.dumps(msg.metadata)
    incomplete = msg.replace(content='Hello\n@shell(rejected): {"command":')
    assert not any(
        i.get("type") == "function_call" for i in _prepare(incomplete, "subscription")
    )
