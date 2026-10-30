"""Offline regression tests for HTTP-200 subscription streams cut short at EOF."""

import json
from collections.abc import Iterator, Sequence
from typing import Any
from unittest.mock import Mock

import pytest
import requests

from gptme.llm import llm_openai_subscription as subscription
from gptme.llm.openai_responses import ResponsesStreamError
from gptme.message import Message


class _Response:
    status_code = 200

    def __init__(self, events: Sequence[dict[str, Any] | str]) -> None:
        self.events = events
        self.closed = False
        self.consumed = 0

    def iter_lines(self) -> Iterator[bytes]:
        for event in self.events:
            self.consumed += 1
            payload = event if isinstance(event, str) else json.dumps(event)
            yield f"data: {payload}".encode()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def post(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setattr(
        subscription,
        "get_auth",
        lambda: subscription.SubscriptionAuth(
            access_token="test-token",
            refresh_token=None,
            account_id="test-account",
            expires_at=9_999_999_999.0,
        ),
    )
    monkeypatch.setenv("GPTME_SUBSCRIPTION_STREAM_RETRIES", "0")
    mock = Mock()
    monkeypatch.setattr(subscription._http_session, "post", mock)
    return mock


def _stream() -> Iterator[str]:
    return subscription.stream([Message("user", "hello")], "gpt-5.4")


@pytest.mark.parametrize(
    ("events", "expected_output", "received_events"),
    [
        ([], [], False),
        ([{"type": "response.created", "response": {"model": "gpt-5.4"}}], [], True),
        (
            [{"type": "response.output_text.delta", "delta": "partial"}],
            ["partial"],
            True,
        ),
        (
            [{"type": "response.reasoning.delta", "delta": "reason"}],
            ["<think>\n", "reason"],
            True,
        ),
        # A valid JSON argument is not a complete response. The parser must not
        # reconcile and expose a native call after a truncated SSE stream.
        (
            [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "name": "shell",
                        "call_id": "call_1",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '{"command":"echo hello"}',
                },
            ],
            [],
            True,
        ),
        (
            [
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": 0,
                    "delta": '{"command":"echo',
                }
            ],
            [],
            True,
        ),
    ],
    ids=[
        "empty",
        "metadata",
        "text",
        "reasoning",
        "valid-native-json",
        "partial-native-json",
    ],
)
def test_eof_is_transport_failure_not_partial_success(
    post: Mock,
    events: list[dict[str, Any]],
    expected_output: list[str],
    received_events: bool,
) -> None:
    response = _Response(events)
    post.return_value = response
    chunks: list[str] = []
    with pytest.raises(requests.exceptions.ChunkedEncodingError) as exc:
        chunks.extend(_stream())
    assert isinstance(exc.value, subscription.SubscriptionStreamIncompleteError)
    assert exc.value.received_events is received_events
    assert exc.value.emitted_output is bool(expected_output)
    assert chunks == expected_output
    assert response.closed
    post.assert_called_once()


@pytest.mark.parametrize("terminal", ["response.completed", "response.done", "[DONE]"])
def test_explicit_terminal_is_success(post: Mock, terminal: str) -> None:
    event: dict[str, Any] | str = (
        terminal if terminal == "[DONE]" else {"type": terminal}
    )
    response = _Response(
        [
            {"type": "response.output_text.delta", "delta": "complete"},
            event,
            {"type": "response.output_text.delta", "delta": "must not read"},
        ]
    )
    post.return_value = response
    assert list(_stream()) == ["complete"]
    assert response.consumed == 2
    assert response.closed


def test_consumer_tool_break_closes_without_demanding_terminal(post: Mock) -> None:
    response = _Response(
        [
            {"type": "response.output_text.delta", "delta": "```shell\necho hi\n```"},
            {"type": "response.output_text.delta", "delta": "not consumed"},
        ]
    )
    post.return_value = response
    gen = subscription.stream([Message("user", "hello")], "gpt-5.4")
    assert next(gen) == "```shell\necho hi\n```"
    gen.close()
    assert response.closed
    assert response.consumed == 1


@pytest.mark.parametrize("event_type", ["error", "response.failed"])
def test_explicit_provider_failure_preserved(post: Mock, event_type: str) -> None:
    error = {"code": "server_error", "message": "failed"}
    event: dict[str, Any] = {"type": event_type}
    if event_type == "error":
        event["error"] = error
    else:
        event["response"] = {"error": error}
    response = _Response([event])
    post.return_value = response
    with pytest.raises(ResponsesStreamError) as exc:
        list(_stream())
    assert exc.value.code == "server_error"
    assert response.closed


def test_explicit_incomplete_retains_existing_semantics(post: Mock) -> None:
    response = _Response(
        [
            {"type": "response.output_text.delta", "delta": "limited"},
            {
                "type": "response.incomplete",
                "response": {
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            },
        ]
    )
    post.return_value = response
    assert list(_stream()) == ["limited"]
    assert response.closed


@pytest.mark.parametrize("visible", [False, True], ids=["metadata-only", "visible"])
def test_eof_after_events_does_not_adapter_retry(
    post: Mock,
    monkeypatch: pytest.MonkeyPatch,
    visible: bool,
) -> None:
    monkeypatch.setenv("GPTME_SUBSCRIPTION_STREAM_RETRIES", "3")
    first = _Response(
        [
            {"type": "response.output_text.delta", "delta": "partial"}
            if visible
            else {"type": "response.created"},
        ]
    )
    second = _Response([{"type": "response.done"}])
    post.side_effect = [first, second]
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        list(_stream())
    post.assert_called_once()
    assert first.closed
    assert not second.closed


def test_empty_eof_uses_bounded_pre_event_retry(
    post: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GPTME_SUBSCRIPTION_STREAM_RETRIES", "1")
    wait = Mock(return_value=False)
    monkeypatch.setattr(subscription, "backoff_wait", wait)
    first = _Response([])
    second = _Response([{"type": "response.done"}])
    post.side_effect = [first, second]
    assert list(_stream()) == []
    assert post.call_count == 2
    wait.assert_called_once()
    assert first.closed and second.closed


@pytest.mark.parametrize(
    "first_body",
    [
        b'data: {"type":"response.created","response":{"model":"gpt-5.4"}}\n\n',
        b"",
    ],
    ids=["metadata-then-eof", "empty-eof"],
)
def test_real_sse_reply_recovery_shares_request_budget(
    monkeypatch: pytest.MonkeyPatch,
    first_body: bytes,
) -> None:
    """Use real requests/SSE parsing and reply(), never a mocked stream iterator."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from gptme import llm
    from gptme.llm import recovery

    bodies: list[bytes] = []
    budgets: list[recovery.RecoveryBudget | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
            payload = (
                first_body
                if len(bodies) == 1
                else (
                    b'data: {"type":"response.output_text.delta","delta":"success"}\n\n'
                    b'data: {"type":"response.completed"}\n\n'
                )
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    monkeypatch.setattr(
        subscription,
        "get_auth",
        lambda: subscription.SubscriptionAuth(
            access_token="test",
            refresh_token=None,
            account_id="test",
            expires_at=9_999_999_999.0,
        ),
    )
    monkeypatch.setattr(llm, "init_llm", lambda *args: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *args: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.setenv("GPTME_SUBSCRIPTION_STREAM_RETRIES", "100")
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 2)
    shared_wait = Mock(return_value=False)
    adapter_wait = Mock(return_value=False)
    monkeypatch.setattr(recovery, "backoff_wait", shared_wait)
    monkeypatch.setattr(subscription, "backoff_wait", adapter_wait)
    original_reserve = recovery.reserve_recovery_request

    def reserve() -> None:
        budgets.append(recovery.get_recovery_budget())
        original_reserve()

    monkeypatch.setattr(recovery, "reserve_recovery_request", reserve)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    monkeypatch.setattr(subscription, "_http_session", session)
    monkeypatch.setattr(
        subscription,
        "CODEX_ENDPOINT",
        f"http://127.0.0.1:{server.server_port}/responses",
    )
    try:
        result = llm.reply(
            [Message("user", "hello")], "openai-subscription/gpt-5.4", stream=True
        )
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        thread.join()
    assert result.content == "success"
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    shared_wait.assert_called_once()
    adapter_wait.assert_not_called()
    assert len(budgets) == 2 and budgets[0] is budgets[1]
    assert budgets[0] is not None and budgets[0].requests == 2
    assert not recovery.recovery_active()


def test_shared_budget_does_not_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A redirect must not resend a POST outside the shared send allowance."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from gptme import llm
    from gptme.llm import recovery

    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            hits.append(self.path)
            self.rfile.read(int(self.headers["Content-Length"]))
            if len(hits) < 3:
                self.send_response(307)
                self.send_header("Location", f"/redirect{len(hits)}")
                body = b""
            else:
                self.send_response(200)
                body = (
                    b'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                    b'data: {"type":"response.completed"}\n\n'
                )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    monkeypatch.setattr(
        subscription,
        "get_auth",
        lambda: subscription.SubscriptionAuth(
            access_token="test",
            refresh_token=None,
            account_id="test",
            expires_at=9_999_999_999.0,
        ),
    )
    monkeypatch.setattr(llm, "init_llm", lambda *a: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(
        llm, "get_model", lambda *a: Mock(supports_parallel_tool_calls=False)
    )
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    session = requests.Session()
    session.trust_env = False
    monkeypatch.setattr(subscription, "_http_session", session)
    monkeypatch.setattr(
        subscription, "CODEX_ENDPOINT", f"http://127.0.0.1:{server.server_port}/start"
    )
    try:
        with pytest.raises(requests.HTTPError):
            llm.reply(
                [Message("user", "hi")], "openai-subscription/gpt-5.4", stream=True
            )
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        thread.join()
    assert hits == ["/start"]
