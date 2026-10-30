"""Bounded replay of an uncommitted LLM generation, never tool execution.

``reply`` is the replay boundary. Direct ``_reply_stream``/``_chat_complete``
callers do not automatically replay. Adapter retry decorators must use
``recovery_active()`` to disable their local retries inside this boundary.
Token callbacks are previews: consumers should bind ``recovery_notifications``
and reset their attempt-local view on each notice. GENERATION_CHUNK hooks may
have irreversible effects (TTS); after invoking one, automatic replay is disabled.
Server session_step uses its own stream loop and is not covered by this boundary.
"""

import logging
import math
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

import httpx
import requests

from .retry_abort import backoff_wait, current_generation
from .retry_policy import _retry_after_seconds, get_max_retries, retry_delay_for_error

logger = logging.getLogger(__name__)
DEFAULT_RECOVERY_SECONDS = 600.0
T = TypeVar("T")
_TRANSIENT_STREAM_CODES = {
    "server_error",
    "internal_error",
    "internal_server_error",
    "rate_limit_exceeded",
    "rate_limit_error",
    "overloaded_error",
    "server_is_overloaded",
    "service_unavailable",
    "service_unavailable_error",
    "temporarily_unavailable",
    "timeout",
}


@dataclass
class RecoveryBudget:
    """One reply's attempt allowance and monotonic replay deadline.

    The deadline bounds starting retries/waits, not a blocking provider request;
    adapter network timeouts remain responsible for individual request duration.
    """

    max_attempts: int
    deadline: float
    attempts: int = 0
    requests: int = 0


class RecoveryBudgetExceeded(RuntimeError):
    """An adapter tried to send after its shared allowance/deadline was spent."""


def _budget_error(message: str) -> RecoveryBudgetExceeded:
    from . import mark_llm_reply_origin

    error = RecoveryBudgetExceeded(message)
    mark_llm_reply_origin(error)
    return error


@dataclass(frozen=True)
class RetryNotice:
    """A failed attempt is discarded; the next attempt has not started yet."""

    attempt: int
    max_attempts: int
    error: Exception
    delay: float
    partial_output: bool


_budget: ContextVar[RecoveryBudget | None] = ContextVar(
    "llm_recovery_budget", default=None
)
_recovering: ContextVar[bool] = ContextVar("llm_recovering", default=False)
_replay_enabled: ContextVar[bool] = ContextVar("llm_replay_enabled", default=True)
_notification: ContextVar[Callable[[RetryNotice], None] | None] = ContextVar(
    "llm_recovery_notification", default=None
)


@contextmanager
def recovery_disabled() -> Iterator[None]:
    """Keep request accounting, but forbid replay for non-resettable consumers.

    Bind in the context that actually consumes the generation (e.g. ACP's
    worker thread), not merely around construction of a lazy step iterator.
    """
    token = _replay_enabled.set(False)
    try:
        yield
    finally:
        _replay_enabled.reset(token)


def get_recovery_budget() -> RecoveryBudget | None:
    """Return the current generation's shared budget, if any."""
    return _budget.get()


def recovery_active() -> bool:
    """Whether adapter-local retries must be suppressed (one provider attempt)."""
    return get_recovery_budget() is not None


@contextmanager
def recovery_scope() -> Iterator[RecoveryBudget]:
    """Share one allowance across reply retries and context-overflow fallbacks.

    This only establishes accounting; recover_generation still owns replay.
    Nested scopes reuse the same mutable budget without resetting its deadline.
    """
    existing = get_recovery_budget()
    if existing is not None:
        yield existing
        return
    attempts = get_max_retries()
    # Keep the existing offline-suite ceiling used by provider decorators. The
    # user-facing policy is still authoritative: this knob can only lower it.
    test_limit = os.environ.get("GPTME_TEST_MAX_RETRIES")
    if test_limit is not None:
        try:
            parsed = int(test_limit)
        except ValueError:
            logger.warning("Invalid GPTME_TEST_MAX_RETRIES value: %r", test_limit)
        else:
            if parsed >= 1:
                attempts = min(attempts, parsed)
    budget = RecoveryBudget(attempts, time.monotonic() + DEFAULT_RECOVERY_SECONDS)
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)


def recovery_remaining_seconds() -> float | None:
    """Remaining monotonic allowance for adapters to cap their network timeout."""
    budget = get_recovery_budget()
    return max(0.0, budget.deadline - time.monotonic()) if budget else None


def reserve_recovery_request() -> None:
    """Debit immediately before an actual provider send (including fallbacks).

    Outside reply recovery this is a no-op. Adapter local retry suppression is
    still needed; this guard additionally counts capability/degeneration resends.
    """
    budget = get_recovery_budget()
    if budget is None:
        return
    if time.monotonic() >= budget.deadline:
        raise _budget_error("LLM recovery deadline exceeded")
    if budget.requests >= budget.max_attempts:
        raise _budget_error("LLM recovery provider request budget exhausted")
    budget.requests += 1


@contextmanager
def recovery_notifications(callback: Callable[[RetryNotice], None]) -> Iterator[None]:
    """Bind an attempt-reset consumer for this context (TUI/server/ACP).

    Called before waiting/replaying, including nonstreaming retries. Callback
    exceptions propagate, never retry. The binding is restored on every exit.
    A default stderr warning still announces discarding partial output.
    """
    token = _notification.set(callback)
    try:
        yield
    finally:
        _notification.reset(token)


def _transient_openai_stream_error(error: BaseException) -> bool:
    """Classify plain APIError from HTTP-200 SSE, not an HTTP status error.

    Explicit codes/types override diagnostic text, so a permanent or unknown
    error never becomes retryable merely by mentioning an overload/timeout.
    Without a code, retain the adapter's supported transient message hints.
    """
    body = getattr(error, "body", None)
    payloads = [body] if isinstance(body, dict) else []
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        payloads.append(body["error"])
    codes = [
        str(payload[key]).lower()
        for payload in payloads
        for key in ("code", "type")
        if payload.get(key) is not None
    ]
    if codes:
        return all(
            (code in _TRANSIENT_STREAM_CODES)
            or (
                code.isdecimal() and (int(code) in (408, 429) or 500 <= int(code) < 600)
            )
            for code in codes
        )
    texts = [getattr(error, "message", "")]
    if isinstance(body, str):
        texts.append(body)
    for payload in payloads:
        texts.extend(
            payload.get(key, "")
            for key in ("error", "message")
            if isinstance(payload.get(key, ""), str)
        )
    combined = " ".join(str(text) for text in texts).lower()
    return any(
        hint in combined
        for hint in ("overload", "internal", "timeout", "rate limit", "server_error")
    )


def is_transient_provider_error(error: BaseException) -> bool:
    """Allowlist actual tagged transport/status failures, not arbitrary SDK errors."""
    if not getattr(error, "_gptme_from_llm_reply", False):
        return False
    if getattr(error, "_gptme_generation_chunk_hook_emitted", False):
        return False
    # These are HTTP-200 application errors disguised as RemoteProtocolError.
    # Only explicitly transient codes are replayable, never unknown/permanent ones.
    from .openai_responses import ResponsesStreamError

    if isinstance(error, ResponsesStreamError):
        return error.code in _TRANSIENT_STREAM_CODES
    if isinstance(
        error,
        (
            requests.Timeout,
            requests.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    if isinstance(
        error, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
    ):
        return True
    # Anthropic can use httpx2. Keep it optional and avoid loading SDKs at startup.
    modules = {(c.__module__ or "").split(".", 1)[0] for c in type(error).__mro__}
    if "httpx2" in modules:
        import httpx2

        if isinstance(
            error,
            (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError),
        ):
            return True
    sdk = bool(modules & {"openai", "anthropic"})
    if sdk and any(
        c.__name__ in {"APIConnectionError", "APITimeoutError"}
        for c in type(error).__mro__
    ):
        return True
    status_error = isinstance(error, (requests.HTTPError, httpx.HTTPStatusError)) or sdk
    if "httpx2" in modules:
        import httpx2

        status_error = status_error or isinstance(error, httpx2.HTTPStatusError)
    if not status_error:
        return False
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status is not None:
        return isinstance(status, int) and (status in (408, 429) or 500 <= status < 600)
    if "openai" in modules:
        from openai import APIError

        if isinstance(error, APIError):
            return _transient_openai_stream_error(error)
    return False


def recover_generation(generate: Callable[[], T]) -> T:
    """Replay a complete uncommitted generation on narrowly allowed failures.

    Nested reply calls share the outer scope and do not create a second loop.
    No Message is returned on exhaustion, deadline, cancellation, or permanent
    failure. The original last exception is raised for interactive resumption.
    """
    if _recovering.get():
        return generate()
    with recovery_scope() as budget:
        return _recover_with_budget(generate, budget)


def _recover_with_budget(generate: Callable[[], T], budget: RecoveryBudget) -> T:
    from .request_limits import bounded_attempts

    # Stop at the strict send ceiling without replacing the last provider error
    # with a later reserve_request ValueError (or sleeping after the last send).
    attempt_limit = bounded_attempts(budget.max_attempts)
    token = _recovering.set(True)
    generation = current_generation()
    try:
        while True:
            if time.monotonic() >= budget.deadline:
                raise _budget_error("LLM recovery deadline exceeded")
            if (
                budget.attempts >= attempt_limit
                or budget.requests >= budget.max_attempts
            ):
                raise _budget_error("LLM recovery provider request budget exhausted")
            budget.attempts += 1
            try:
                return generate()
            except Exception as error:
                if (
                    not _replay_enabled.get()
                    or not is_transient_provider_error(error)
                    or budget.attempts >= attempt_limit
                    or budget.requests >= budget.max_attempts
                ):
                    raise
                # The shared policy may cap Retry-After for legacy adapter callers.
                # Never replay sooner than a provider's raw Retry-After here.
                delay = max(
                    retry_delay_for_error(error, budget.attempts - 1),
                    _retry_after_seconds(error) or 0.0,
                )
                remaining = budget.deadline - time.monotonic()
                if not math.isfinite(delay) or delay >= remaining:
                    raise
                notice = RetryNotice(
                    budget.attempts,
                    budget.max_attempts,
                    error,
                    delay,
                    bool(getattr(error, "_gptme_llm_reply_output_emitted", False)),
                )
                logger.warning(
                    "Discarding %sLLM attempt %d/%d after %s; retrying in %.1fs",
                    "partial " if notice.partial_output else "failed ",
                    notice.attempt,
                    notice.max_attempts,
                    type(error).__name__,
                    delay,
                )
                callback = _notification.get()
                if callback is not None:
                    callback(notice)
                # Consumer reset can itself take time; never begin a wait or a
                # provider attempt after the monotonic budget has expired.
                if delay >= budget.deadline - time.monotonic():
                    raise
                if (
                    backoff_wait(delay, generation)
                    or time.monotonic() >= budget.deadline
                ):
                    raise
    finally:
        _recovering.reset(token)
