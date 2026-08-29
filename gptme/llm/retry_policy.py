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
