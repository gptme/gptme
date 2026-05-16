import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

from gptme.llm import llm_openai_subscription
from gptme.llm.llm_openai_subscription import SubscriptionAuth
from gptme.message import Message
from gptme.tools import get_tool, init_tools


def _make_auth() -> SubscriptionAuth:
    return SubscriptionAuth(
        access_token="test-token",
        refresh_token=None,
        account_id="test-account",
        expires_at=9_999_999_999.0,
    )


class _FakeSSEStreamResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.status_code = 200
        self.text = ""
        self._events = events

    def iter_lines(self) -> Iterator[bytes]:
        for event in self._events:
            yield f"data: {json.dumps(event)}".encode()


def _run_stream(events: list[dict[str, Any]]) -> str:
    auth = _make_auth()
    response = _FakeSSEStreamResponse(events)

    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=auth),
        patch("gptme.llm.llm_openai_subscription.requests.post", return_value=response),
    ):
        return "".join(
            llm_openai_subscription.stream(
                [Message(role="user", content="hello")], "gpt-5.4"
            )
        )


def test_stream_wraps_reasoning_and_closes_before_text():
    output = _run_stream(
        [
            {"type": "response.reasoning.delta", "delta": "Need a command"},
            {"type": "response.output_text.delta", "delta": "Done."},
            {"type": "response.done"},
        ]
    )

    assert output == "<think>\nNeed a command\n</think>\nDone."


def test_stream_converts_split_thinking_tags_across_chunks():
    output = _run_stream(
        [
            {"type": "response.output_text.delta", "delta": "Before <thi"},
            {"type": "response.output_text.delta", "delta": "nking>reason"},
            {"type": "response.output_text.delta", "delta": "ing</think"},
            {"type": "response.output_text.delta", "delta": "ing> after"},
            {"type": "response.done"},
        ]
    )

    assert output == "Before <think>reasoning</think> after"


def test_stream_ignores_output_text_done_to_avoid_duplicate_text():
    output = _run_stream(
        [
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.done", "text": "Hello"},
            {"type": "response.done"},
        ]
    )

    assert output == "Hello"


def test_stream_closes_reasoning_before_function_call_output():
    output = _run_stream(
        [
            {"type": "response.reasoning.delta", "delta": "Need save"},
            {
                "type": "response.output_item.added",
                "item": {
                    "type": "function_call",
                    "name": "save",
                    "call_id": "call_1",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "delta": '{"path":"x.txt"}',
            },
            {"type": "response.done"},
        ]
    )

    assert output == '<think>\nNeed save\n</think>\n\n@save(call_1): {"path":"x.txt"}'


def test_stream_no_double_wrap_when_both_mechanisms_fire():
    """Regression: gpt-5.4 can emit BOTH response.reasoning.delta AND raw <thinking>
    tags in output_text.delta for the same content. Without the fix this produces
    nested <think><think>...</think></think> double-wrapping.
    """
    output = _run_stream(
        [
            # Structured reasoning events — open the <think> block
            {"type": "response.reasoning.delta", "delta": "Need a command"},
            # Model ALSO echoes reasoning as raw <thinking> in text output (gpt-5.4 bug).
            # The text conversion must be skipped to avoid double-wrapping.
            {
                "type": "response.output_text.delta",
                "delta": "<thinking>Need a command</thinking>",
            },
            {"type": "response.output_text.delta", "delta": "Done."},
            {"type": "response.done"},
        ]
    )

    assert output == "<think>\nNeed a command\n</think>\nDone."


def test_stream_builds_shared_responses_request_shape():
    response = _FakeSSEStreamResponse([{"type": "response.done"}])
    init_tools(allowlist=["save"])
    save_tool = get_tool("save")
    assert save_tool is not None

    messages = [
        Message(role="system", content="You are concise."),
        Message(role="user", content="Save a note."),
        Message(
            role="assistant",
            content='Saving now.\n@save(call_123): {"path": "note.txt", "content": "hi"}',
        ),
        Message(role="system", content="Saved to note.txt", call_id="call_123"),
    ]

    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=_make_auth()),
        patch(
            "gptme.llm.llm_openai_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(llm_openai_subscription.stream(messages, "gpt-5.4", tools=[save_tool]))

    request_json = mock_post.call_args.kwargs["json"]
    assert request_json["instructions"] == "You are concise."
    assert request_json["input"] == [
        {"role": "user", "content": "Save a note."},
        {"role": "assistant", "content": "Saving now."},
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "save",
            "arguments": '{"path": "note.txt", "content": "hi"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Saved to note.txt",
        },
    ]
    assert request_json["tools"][0]["type"] == "function"
    assert request_json["tools"][0]["name"] == "save"


def test_stream_forwards_max_tokens_as_max_output_tokens():
    """max_tokens passed to stream() must appear as max_output_tokens in the POST body."""
    response = _FakeSSEStreamResponse([{"type": "response.done"}])

    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=_make_auth()),
        patch(
            "gptme.llm.llm_openai_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(
            llm_openai_subscription.stream(
                [Message(role="user", content="hello")],
                "gpt-5.4",
                max_tokens=1000,
            )
        )

    request_json = mock_post.call_args.kwargs["json"]
    assert request_json["max_output_tokens"] == 1000


def test_stream_omits_max_output_tokens_when_not_provided():
    """When max_tokens is not given, max_output_tokens must not appear in the POST body."""
    response = _FakeSSEStreamResponse([{"type": "response.done"}])

    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=_make_auth()),
        patch(
            "gptme.llm.llm_openai_subscription.requests.post", return_value=response
        ) as mock_post,
    ):
        list(
            llm_openai_subscription.stream(
                [Message(role="user", content="hello")], "gpt-5.4"
            )
        )

    request_json = mock_post.call_args.kwargs["json"]
    assert "max_output_tokens" not in request_json
