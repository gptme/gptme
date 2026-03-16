import json
from unittest.mock import Mock

from gptme.llm import llm_openai_subscription
from gptme.message import Message


def _sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event)}".encode()


def _mock_response(lines: list[bytes]) -> Mock:
    response = Mock()
    response.status_code = 200
    response.text = ""
    response.iter_lines.return_value = lines
    return response


def _mock_auth() -> llm_openai_subscription.SubscriptionAuth:
    return llm_openai_subscription.SubscriptionAuth(
        access_token="token",
        refresh_token=None,
        account_id="account",
        expires_at=0,
    )


def test_stream_wraps_reasoning_deltas_before_text(monkeypatch) -> None:
    response = _mock_response(
        [
            _sse({"type": "response.reasoning.delta", "delta": "step 1"}),
            _sse({"type": "response.reasoning.delta", "delta": " + step 2"}),
            _sse({"type": "response.output_text.delta", "delta": "Answer"}),
            _sse({"type": "response.done"}),
        ]
    )
    monkeypatch.setattr(llm_openai_subscription, "get_auth", _mock_auth)
    monkeypatch.setattr(
        llm_openai_subscription.requests, "post", lambda *a, **k: response
    )

    output = "".join(
        llm_openai_subscription.stream(
            [Message("user", "hi")], "openai-subscription/gpt-5.4"
        )
    )

    assert output == "<think>\nstep 1 + step 2\n</think>\nAnswer"


def test_stream_converts_split_thinking_tags_in_text_deltas(monkeypatch) -> None:
    response = _mock_response(
        [
            _sse({"type": "response.output_text.delta", "delta": "Before <thin"}),
            _sse({"type": "response.output_text.delta", "delta": "king>plan</thin"}),
            _sse({"type": "response.output_text.delta", "delta": "king> After"}),
            _sse({"type": "response.done"}),
        ]
    )
    monkeypatch.setattr(llm_openai_subscription, "get_auth", _mock_auth)
    monkeypatch.setattr(
        llm_openai_subscription.requests, "post", lambda *a, **k: response
    )

    output = "".join(
        llm_openai_subscription.stream(
            [Message("user", "hi")], "openai-subscription/gpt-5.4"
        )
    )

    assert output == "Before <think>plan</think> After"


def test_stream_closes_reasoning_before_tool_call(monkeypatch) -> None:
    response = _mock_response(
        [
            _sse({"type": "response.reasoning.delta", "delta": "Need to save this"}),
            _sse(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "name": "save",
                        "call_id": "call-1",
                    },
                }
            ),
            _sse(
                {
                    "type": "response.function_call_arguments.delta",
                    "delta": '{"path": "x.txt"}',
                }
            ),
            _sse({"type": "response.done"}),
        ]
    )
    monkeypatch.setattr(llm_openai_subscription, "get_auth", _mock_auth)
    monkeypatch.setattr(
        llm_openai_subscription.requests, "post", lambda *a, **k: response
    )

    output = "".join(
        llm_openai_subscription.stream(
            [Message("user", "hi")], "openai-subscription/gpt-5.4"
        )
    )

    assert (
        output
        == '<think>\nNeed to save this\n</think>\n\n@save(call-1): {"path": "x.txt"}'
    )
