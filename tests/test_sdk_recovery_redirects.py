"""SDK redirects must not issue unaccounted POSTs within reply recovery."""

from typing import Any

import anthropic
import httpx
import httpx2
import openai
import pytest

from gptme.llm import llm_anthropic, llm_openai
from gptme.llm.recovery import recovery_scope
from gptme.message import Message


@pytest.mark.parametrize("provider", ["anthropic", "openai", "responses"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [307, 308])
def test_recovery_sdk_redirect_is_one_post(
    monkeypatch: pytest.MonkeyPatch, provider: str, stream: bool, status: int
) -> None:
    monkeypatch.setenv("GPTME_LLM_MAX_RETRIES", "1")
    module: Any = httpx2 if provider == "anthropic" else httpx
    sent: list[Any] = []
    owned: list[Any] = []

    def handle(request: Any) -> Any:
        sent.append(request)
        return module.Response(
            status,
            headers={"location": "https://unapproved.invalid/replay"},
            json={"error": {"message": "redirect refused"}},
        )

    def make_transport(**kwargs: Any) -> Any:
        transport = module.Client(transport=module.MockTransport(handle), **kwargs)
        owned.append(transport)
        return transport

    original = module.Client(
        transport=module.MockTransport(handle), follow_redirects=True
    )
    if provider == "anthropic":
        client: Any = anthropic.Anthropic(
            api_key="offline",
            max_retries=0,
            http_client=original,
            base_url="https://custom.invalid/",
        )
        monkeypatch.setattr(anthropic, "DefaultHttpxClient", make_transport)
        monkeypatch.setattr(llm_anthropic, "_anthropic", client)
        run = llm_anthropic.stream if stream else llm_anthropic.chat
        model = "claude-sonnet-4-6"
        error: Any = anthropic.APIStatusError
    else:
        client = openai.OpenAI(
            api_key="offline",
            max_retries=0,
            http_client=original,
            base_url="https://custom.invalid/",
        )
        monkeypatch.setattr(openai, "DefaultHttpxClient", make_transport)
        monkeypatch.setattr(llm_openai, "get_client", lambda _: client)
        monkeypatch.setattr(llm_openai, "_is_proxy", lambda _: False)
        monkeypatch.setattr(
            llm_openai, "_should_use_responses_api", lambda *a: provider == "responses"
        )
        run = llm_openai.stream if stream else llm_openai.chat
        model = "openai/gpt-4o"
        error = openai.APIStatusError
    try:
        with recovery_scope() as budget:
            assert budget.max_attempts == 1
            with pytest.raises(error) as exc:
                result = run(
                    [Message("system", ""), Message("user", "hi")], model, None
                )
                if stream:
                    list(result)
            assert exc.value.status_code == status
            assert budget.requests == 1
        assert len(sent) == 1
        assert sent[0].method == "POST"
        assert sent[0].url.host == "custom.invalid"
        assert original.follow_redirects is True
        assert not original.is_closed
        assert owned and all(transport.is_closed for transport in owned)
    finally:
        client.close()
