"""Shared generation recovery must not multiply provider adapter retries."""

import httpx
import pytest


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
def test_shared_recovery_disables_nested_adapter_retry(monkeypatch, provider, stream):
    from gptme.llm import recovery

    if provider == "openai":
        from gptme.llm.llm_openai import (
            retry_generator_on_openai_error,
            retry_on_openai_error,
        )

        decorate = retry_generator_on_openai_error if stream else retry_on_openai_error
    else:
        from gptme.llm.llm_anthropic import (
            retry_generator_on_overloaded,
            retry_on_overloaded,
        )

        decorate = retry_generator_on_overloaded if stream else retry_on_overloaded

    monkeypatch.setattr(recovery, "recovery_active", lambda: True)
    calls = []

    def fail():
        calls.append(1)
        raise httpx.RemoteProtocolError("fixture disconnect")

    def fail_stream():
        fail()
        yield "unreachable"

    run = decorate(max_retries=3, base_delay=0)(fail_stream if stream else fail)
    with pytest.raises(httpx.RemoteProtocolError):
        result = run()
        if stream:
            list(result)
    assert len(calls) == 1
