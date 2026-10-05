"""Explicit Responses SSE failures must not become successful completions."""

from types import SimpleNamespace

import httpx
import pytest

from gptme.llm.openai_responses import _stream_responses_events


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
        httpx.RemoteProtocolError, match="server_is_overloaded.*Try later"
    ):
        list(_stream_responses_events(events))


@pytest.mark.parametrize("event_type", ["error", "response.failed"])
def test_failure_without_details_still_raises(event_type: str) -> None:
    with pytest.raises(httpx.RemoteProtocolError, match=event_type):
        list(_stream_responses_events([{"type": event_type}]))


@pytest.mark.parametrize("terminal", ["response.completed", "response.done"])
def test_successful_stream_is_unchanged(terminal: str) -> None:
    events = [{"type": "response.output_text.delta", "delta": "ok"}, {"type": terminal}]
    assert "".join(_stream_responses_events(events)) == "ok"


@pytest.mark.parametrize("partial", [False, True])
def test_subscription_cli_failure_is_nonzero_and_closes_stream(partial: bool) -> None:
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
            result = CliRunner().invoke(
                llm_generate, ["--model", "openai-subscription/gpt-5.6-sol", "hello"]
            )
    finally:
        logging.getLogger().setLevel(level)
    assert result.exit_code == 1
    assert "server_is_overloaded" in result.output
    assert "private instructions" not in result.output
    assert "partial" not in result.output
    assert response.closed
    assert post.call_count == 1
