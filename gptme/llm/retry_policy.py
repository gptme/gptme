"""Shared retry policy for LLM provider calls.

gptme owns the retry/backoff policy for LLM requests. The provider SDKs
(openai, anthropic) also retry internally by default, which multiplies with
gptme's own retry loop: a single 429 turned into ``sdk_retries * max_retries``
requests, all inside a backoff window too short to outlast a rate-limit blip
(see https://github.com/gptme/gptme/issues/3668).

So: SDK-level retries are disabled (``SDK_MAX_RETRIES = 0``) for every client
constructed through ``llm_openai`` / ``llm_anthropic`` (including OpenAI-compatible
providers and grok-subscription). ``openai-subscription`` talks to ChatGPT's
backend with ``requests`` and has its own stream-retry loop — it does not use
an OpenAI SDK client. This module is the single place that decides how many
attempts gptme makes and how long it waits between them.

The default budget is ~5 minutes of cumulative backoff (1, 2, 4, 8, 16, 32,
then 60s per attempt), so a short upstream outage does not kill a long
autonomous session. Override with ``GPTME_LLM_MAX_RETRIES``.
"""

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

# Provider SDKs must not retry — gptme's retry decorators own the policy.
SDK_MAX_RETRIES = 0

# Attempts (including the first, non-retry attempt) made by gptme's decorators.
# 11 attempts with the delays below is ~303s (~5 min) of cumulative backoff.
DEFAULT_MAX_RETRIES = 11

# Exponential backoff: base_delay * 2**attempt, capped at MAX_RETRY_DELAY.
DEFAULT_BASE_DELAY = 1.0
MAX_RETRY_DELAY = 60.0


def get_max_retries(default: int = DEFAULT_MAX_RETRIES) -> int:
    """Number of attempts for LLM calls, overridable via GPTME_LLM_MAX_RETRIES."""
    from ..config import get_config  # fmt: skip

    raw = get_config().get_env("GPTME_LLM_MAX_RETRIES")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid GPTME_LLM_MAX_RETRIES value: %r. Must be an integer, using %d.",
            raw,
            default,
        )
        return default
    if value < 1:
        logger.warning(
            "GPTME_LLM_MAX_RETRIES must be >= 1, got %d, using %d.", value, default
        )
        return default
    return value


def retry_delay(attempt: int, base_delay: float = DEFAULT_BASE_DELAY) -> float:
    """Capped exponential backoff delay for a zero-indexed attempt."""
    return min(base_delay * (2**attempt), MAX_RETRY_DELAY)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _retry_after_seconds(error: Any) -> float | None:
    """Parse a provider response's Retry-After header, if present and valid."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = (retry_at - _utcnow()).total_seconds()
    return delay if delay >= 0 else None


def retry_delay_for_error(
    error: Any, attempt: int, base_delay: float = DEFAULT_BASE_DELAY
) -> float:
    """Return exponential backoff extended by Retry-After, within the delay cap."""
    exponential = retry_delay(attempt, base_delay)
    retry_after = _retry_after_seconds(error)
    if retry_after is None:
        return exponential
    return min(max(exponential, retry_after), MAX_RETRY_DELAY)
