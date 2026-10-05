"""Explicit Responses SSE failures must not become successful completions."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from gptme.llm import is_context_length_error, mark_llm_reply_origin
from gptme.llm.llm_openai import _handle_openai_transient_error
from gptme.llm.openai_responses import (
    ResponsesStreamError,
    _stream_responses_events,
)


@pytest.mark.parametrize(
    "event",
    [
        {
            "type": "error",
            "error": {"code": "server_is_overloaded", "message": "Try later"},
        },
        {"type": "error", "code": "server_is_overloaded", "message": "Try later"},
        {
            "type": "response.failed",
            "response": {
                "error": {"code": "server_is_overloaded", "message": "Try later"}
            },
        },
        SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(
                error=SimpleNamespace(code="server_is_overloaded", message="Try later")
            ),
        ),
    ],
)
@pytest.mark.parametrize("partial", [False, True])
def test_explicit_failure_raises(event, partial: bool) -> None:
    events = (
        [{"type": "response.output_text.delta", "delta": "partial"}] if partial else []
    ) + [event]
    with pytest.raises(
        ResponsesStreamError, match="server_is_overloaded.*Try later"
    ) as exc_info:
        list(_stream_responses_events(events))
    assert isinstance(exc_info.value, httpx.RemoteProtocolError)
    assert exc_info.value.code == "server_is_overloaded"


@pytest.mark.parametrize("event_type", ["error", "response.failed"])
def test_failure_without_details_still_raises(event_type: str) -> None:
    with pytest.raises(httpx.RemoteProtocolError, match=event_type):
        list(_stream_responses_events([{"type": event_type}]))


@pytest.mark.parametrize("terminal", ["response.completed", "response.done"])
def test_successful_stream_is_unchanged(terminal: str) -> None:
    events = [{"type": "response.output_text.delta", "delta": "ok"}, {"type": terminal}]
    assert "".join(_stream_responses_events(events)) == "ok"


def test_context_length_code_triggers_recovery_without_message_phrase() -> None:
    error = ResponsesStreamError(
        "response.failed", "context_length_exceeded", "Request failed"
    )
    mark_llm_reply_origin(error)
    assert is_context_length_error(error)


def test_permanent_responses_failure_is_not_retried() -> None:
    error = ResponsesStreamError(
        "response.failed", "insufficient_quota", "Top up your account"
    )
    with pytest.raises(ResponsesStreamError) as exc_info:
        _handle_openai_transient_error(error, 0, 3, 0)
    assert exc_info.value is error


@pytest.mark.parametrize(
    "code",
    ["rate_limit_exceeded", "server_is_overloaded", "service_unavailable_error"],
)
def test_transient_responses_failure_is_retried(monkeypatch, code: str) -> None:
    wait = Mock(return_value=False)
    monkeypatch.setattr("gptme.llm.llm_openai.backoff_wait", wait)
    error = ResponsesStreamError("response.failed", code, "Try later")
    _handle_openai_transient_error(error, 0, 3, 0)
    wait.assert_called_once()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_subscription_cli_failure_is_nonzero_and_closes_stream(
    partial: bool, stream: bool
) -> None:
    import json
    import logging
    from unittest.mock import patch

    from click.testing import CliRunner

    from gptme.cli.util import llm_generate
    from gptme.llm import llm_openai_subscription

    class Response:
        status_code = 200
        closed = False

        def iter_lines(self):
            events = (
                [{"type": "response.output_text.delta", "delta": "partial"}]
                if partial
                else []
            ) + [
                {
                    "type": "response.failed",
                    "response": {
                        "instructions": "private instructions",
                        "error": {
                            "code": "server_is_overloaded",
                            "message": "Try later",
                        },
                    },
                }
            ]
            for event in events:
                yield f"data: {json.dumps(event)}".encode()

        def close(self) -> None:
            self.closed = True

    response = Response()
    auth = SimpleNamespace(access_token="test", account_id="test")
    level = logging.getLogger().level
    try:
        with (
            patch("gptme.init.init"),
            patch("gptme.llm.init_llm"),
            patch.object(llm_openai_subscription, "get_auth", return_value=auth),
            patch.object(
                llm_openai_subscription.requests, "post", return_value=response
            ) as post,
        ):
            args = ["--model", "openai-subscription/gpt-5.6-sol"]
            if stream:
                args.append("--stream")
            result = CliRunner().invoke(llm_generate, [*args, "hello"])
    finally:
        logging.getLogger().setLevel(level)
    assert result.exit_code == 1
    assert "server_is_overloaded" in result.output
    assert "private instructions" not in result.output
    assert ("partial" in result.output) is (stream and partial)
    assert response.closed
    assert post.call_count == 1
