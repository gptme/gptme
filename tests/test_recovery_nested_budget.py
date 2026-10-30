"""Shared generation recovery must not multiply provider adapter retries."""

from collections.abc import Iterator
from typing import Literal, NoReturn

import httpx
import httpx2
import pytest


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("recovery_active", [False, True])
def test_shared_recovery_disables_nested_adapter_retry(
    monkeypatch: pytest.MonkeyPatch,
    provider: Literal["openai", "anthropic"],
    stream: bool,
    recovery_active: bool,
) -> None:
    from gptme.llm import recovery

    error_type: type[Exception]
    if provider == "openai":
        from gptme.llm.llm_openai import (
            retry_generator_on_openai_error,
            retry_on_openai_error,
        )

        decorate = retry_generator_on_openai_error if stream else retry_on_openai_error
        error_type = httpx.RemoteProtocolError
    else:
        from gptme.llm.llm_anthropic import (
            retry_generator_on_overloaded,
            retry_on_overloaded,
        )

        decorate = retry_generator_on_overloaded if stream else retry_on_overloaded
        error_type = httpx2.RemoteProtocolError

    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    monkeypatch.setattr(recovery, "recovery_active", lambda: recovery_active)
    calls: list[int] = []

    def fail() -> NoReturn:
        calls.append(1)
        raise error_type("fixture disconnect")

    def fail_stream() -> Iterator[str]:
        fail()
        yield "unreachable"

    run = decorate(max_retries=3, base_delay=0)(fail_stream if stream else fail)
    with pytest.raises(error_type):
        result = run()
        if stream:
            list(result)
    # The inactive control proves the exception actually exercises adapter retries.
    assert len(calls) == (1 if recovery_active else 3)
