"""Physical-send exhaustion must preserve the last provider failure."""

from unittest.mock import Mock

import httpx
import pytest

from gptme.llm import is_llm_reply_error, mark_llm_reply_origin, recovery
from gptme.llm.request_limits import request_limits, reserve_request


@pytest.mark.parametrize("strict_limit", [2, 3])
def test_fallback_sends_recheck_strict_allowance(
    monkeypatch: pytest.MonkeyPatch, strict_limit: int
) -> None:
    monkeypatch.setattr(recovery, "get_max_retries", lambda: 5)
    monkeypatch.delenv("GPTME_TEST_MAX_RETRIES", raising=False)
    wait = Mock(return_value=False)
    monkeypatch.setattr(recovery, "backoff_wait", wait)
    client = Mock(base_url="https://openrouter.ai/api/v1", max_retries=0)
    client._client.follow_redirects = False
    error = httpx.HTTPStatusError(
        "upstream unavailable",
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
        response=httpx.Response(503),
    )
    mark_llm_reply_origin(error)
    attempts: list[int] = []
    sends: list[int] = []
    notices: list[recovery.RetryNotice] = []

    def generate() -> str:
        attempts.append(1)
        # A capability fallback can spend multiple sends in one generation.
        for _ in range(2 if len(attempts) == 1 else 1):
            reserve_request(client, "openrouter", {"messages": []})
            recovery.reserve_recovery_request()
            sends.append(1)
        if len(attempts) == 1:
            raise error
        return "complete"

    with (
        request_limits("openrouter/offline", 1000, strict_limit),
        recovery.recovery_scope() as budget,
        recovery.recovery_notifications(notices.append),
    ):
        if strict_limit == 2:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                recovery.recover_generation(generate)
            assert caught.value is error
            assert is_llm_reply_error(caught.value)
        else:
            assert recovery.recover_generation(generate) == "complete"
        assert budget.requests == len(sends) == strict_limit
        assert budget.attempts == len(attempts) == strict_limit - 1
    assert wait.call_count == len(notices) == strict_limit - 2
