"""Offline coverage for HTTP-200 SSE errors inside generation recovery."""

from collections.abc import Iterator

import httpx
import openai
import pytest

from gptme.llm import mark_llm_reply_origin, recovery
from gptme.llm.llm_openai import retry_generator_on_openai_error
from gptme.llm.openai_responses import ResponsesStreamError


def stream_error(message: str, body: object = None) -> openai.APIError:
    return openai.APIError(
        message,
        request=httpx.Request("POST", "https://offline.invalid/v1/chat/completions"),
        body=body,
    )


@pytest.mark.parametrize(
    ("message", "body", "transient"),
    [
        ("Overloaded", {"code": 502}, True),
        ("fixture", {"code": 503}, True),
        ("fixture", {"code": 408}, True),
        ("fixture", {"code": 599}, True),
        ("fixture", {"code": "server_error", "type": "invalid_request_error"}, False),
        ("fixture", {"code": 502, "error": {"code": 401}}, False),
        ("fixture", {"code": "429"}, True),
        ("fixture", {"error": {"code": 502}}, True),
        ("Overloaded", None, True),
        ("Internal server error", None, True),
        ("Request timeout", None, True),
        ("Rate limit reached", None, True),
        ("server_error", None, True),
        ("fixture", {"error": "Overloaded"}, True),
        ("fixture", {"message": "upstream timeout"}, True),
        ("fixture", {"error": {"type": "overloaded_error"}}, True),
        ("fixture", {"code": "server_is_overloaded"}, True),
        ("fixture", {"code": "service_unavailable_error"}, True),
        ("Invalid request", {"code": 400}, False),
        ("Overloaded", {"code": 401}, False),
        ("Internal error", {"code": "402"}, False),
        ("timeout", {"error": {"code": 403}}, False),
        ("Overloaded", {"code": 200}, False),
        ("Overloaded", {"code": 600}, False),
        ("Overloaded", {"code": "invalid_api_key"}, False),
        ("Overloaded", {"code": "insufficient_quota"}, False),
        ("Overloaded", {"error": {"type": "invalid_request_error"}}, False),
        ("Overloaded", {"code": "context_length_exceeded"}, False),
        ("Overloaded", {"code": "unknown"}, False),
        ("Invalid request", None, False),
        ("fixture", {"metadata": "Overloaded"}, False),
    ],
)
def test_plain_sse_error_classification(
    message: str, body: object, transient: bool
) -> None:
    error = stream_error(message, body)
    mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error) is transient


@pytest.mark.parametrize(
    "status", [200, 400, 401, 402, 403, 408, 429, 500, 502, 599, 600]
)
def test_http_status_takes_precedence_over_body(status: int) -> None:
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://offline.invalid")
    )
    error = openai.APIStatusError("Overloaded", response=response, body={"code": 502})
    mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error) is (
        status in (408, 429) or 500 <= status < 600
    )


def test_provenance_and_irreversible_hook_gates() -> None:
    error = stream_error("Overloaded", {"code": 502})
    assert not recovery.is_transient_provider_error(error)
    mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error)
    error._gptme_generation_chunk_hook_emitted = True  # type: ignore[attr-defined]
    assert not recovery.is_transient_provider_error(error)
    unrelated = ValueError("Overloaded timeout server_error")
    mark_llm_reply_origin(unrelated)
    assert not recovery.is_transient_provider_error(unrelated)


@pytest.mark.parametrize("code", ["server_is_overloaded", "service_unavailable_error"])
def test_existing_responses_transient_codes(code: str) -> None:
    error = ResponsesStreamError("response.failed", code, "fixture")
    mark_llm_reply_origin(error)
    assert recovery.is_transient_provider_error(error)


@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_recovery_owns_sse_retry_with_adapter_retries_disabled(
    monkeypatch: pytest.MonkeyPatch, permanent: bool, partial: bool
) -> None:
    """Consume a real decorated generator, then replay only the transient error."""
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    waits: list[float] = []

    def wait(delay: float, generation: int | None) -> bool:
        waits.append(delay)
        return False

    monkeypatch.setattr(recovery, "backoff_wait", wait)
    attempts: list[int] = []
    error = stream_error("Overloaded", {"code": 401 if permanent else 502})

    @retry_generator_on_openai_error(max_retries=3)
    def stream() -> Iterator[str]:
        assert recovery.recovery_active()
        recovery.reserve_recovery_request()
        attempts.append(1)
        if len(attempts) == 1:
            if partial:
                yield "discarded preview"
            raise error
        yield "complete"

    def generate() -> str:
        try:
            return "".join(stream())
        except openai.APIError as exc:
            mark_llm_reply_origin(exc)
            raise

    notices: list[recovery.RetryNotice] = []
    with recovery.recovery_notifications(notices.append):
        if permanent:
            with pytest.raises(openai.APIError) as caught:
                recovery.recover_generation(generate)
            assert caught.value is error
        else:
            assert recovery.recover_generation(generate) == "complete"
    assert len(attempts) == (1 if permanent else 2)
    assert len(waits) == len(notices) == (0 if permanent else 1)
    assert not recovery.recovery_active()


@pytest.mark.parametrize("code", [502, 503, 401])
def test_sdk_sse_error_recovered_by_reply(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Real SDK SSE decoding, adapter suppression, and whole-reply recovery."""
    import json

    from gptme import llm
    from gptme.llm import llm_openai
    from gptme.message import Message

    monkeypatch.setattr(llm, "init_llm", lambda _: None)
    monkeypatch.setattr(llm, "is_output_quiet", lambda: True)
    monkeypatch.setattr(llm_openai, "_is_proxy", lambda _: False)
    monkeypatch.setattr(llm_openai, "_should_use_responses_api", lambda *a: False)
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 3)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "backoff_wait", lambda *a: False)

    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert recovery.recovery_active()
        sent.append(request)
        body: dict[str, object]
        if len(sent) == 1:
            body = {"error": {"message": "Overloaded", "code": code}}
        else:
            body = {
                "id": "chatcmpl_offline",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "delta": {"role": "assistant", "content": "complete"},
                    }
                ],
            }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n",
        )

    with (
        httpx.Client(
            transport=httpx.MockTransport(handle), follow_redirects=False
        ) as transport,
        openai.OpenAI(
            api_key="offline",
            max_retries=0,
            http_client=transport,
            base_url="https://offline.invalid/v1",
        ) as client,
    ):
        monkeypatch.setitem(llm_openai.clients, "openai", client)
        notices: list[recovery.RetryNotice] = []
        with recovery.recovery_scope() as budget:
            with recovery.recovery_notifications(notices.append):
                if code == 401:
                    with pytest.raises(openai.APIError):
                        llm.reply(
                            [Message("system", ""), Message("user", "hi")],
                            "openai/gpt-4o",
                            stream=True,
                            max_tokens=16,
                        )
                else:
                    result = llm.reply(
                        [Message("system", ""), Message("user", "hi")],
                        "openai/gpt-4o",
                        stream=True,
                        max_tokens=16,
                    )
                    assert result.content == "complete"
            assert budget.requests == budget.attempts == (1 if code == 401 else 2)
        assert len(sent) == (1 if code == 401 else 2)
        assert len(notices) == (0 if code == 401 else 1)
        assert all(request.method == "POST" for request in sent)
        assert all(request.url.host == "offline.invalid" for request in sent)
