import json
from typing import Any
from unittest.mock import patch

from gptme.llm.llm_openai_subscription import SubscriptionAuth, stream
from gptme.message import Message


class _FakeSSEStreamResponse:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.status_code = 200
        self.text = ""
        self._events = events

    def iter_lines(self):  # type: ignore[no-untyped-def]
        for event in self._events:
            yield f"data: {json.dumps(event)}".encode()


def _run_stream(events: list[dict[str, Any]]) -> str:
    auth = SubscriptionAuth(
        access_token="test-token",
        refresh_token=None,
        account_id="test-account",
        expires_at=9_999_999_999.0,
    )
    response = _FakeSSEStreamResponse(events)

    with (
        patch("gptme.llm.llm_openai_subscription.get_auth", return_value=auth),
        patch("gptme.llm.llm_openai_subscription.requests.post", return_value=response),
    ):
        return "".join(stream([Message(role="user", content="hello")], "gpt-5.4"))


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

    # Should produce exactly one <think> block, not nested <think><think>
    assert "<think><think>" not in output
    assert output.count("<think>") == 1
    assert output.count("</think>") == 1
    assert "Done." in output
