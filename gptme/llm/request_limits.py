"""Opt-in bounds for nonstreaming text selector calls on audited API routes.

Input bytes bound UTF-8 text after hooks and serialized provider input, not a
model-independent token count. Currency reservations must also allow for provider
framing and use a verified price schedule. Failed sends consume their allowance.
The allowance covers generation after hooks, not arbitrary work performed by
hooks themselves; selector workers must start without generation hooks.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ..message import Message


@dataclass
class _RequestLimits:
    provider: str
    input_bytes: int
    requests: int
    used: int = 0


_limits: ContextVar[_RequestLimits | None] = ContextVar(
    "llm_request_limits", default=None
)
_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com",
    "openrouter": "https://openrouter.ai/api/v1",
}


def validate_limits(
    messages: list[Message],
    model: str,
    max_input_bytes: int | None,
    max_provider_requests: int | None,
    max_tokens: int | None,
    *,
    stream: bool,
    tools: Any,
    output_schema: Any,
) -> None:
    """Reject unsupported strict calls before provider initialization."""
    if max_input_bytes is None and max_provider_requests is None:
        return
    if any(
        type(value) is not int or value <= 0
        for value in (max_input_bytes, max_provider_requests, max_tokens)
    ):
        raise ValueError(
            "strict request bounds and max_tokens must be positive integers"
        )
    provider, slash, name = model.partition("/")
    if provider not in _ENDPOINTS or not slash or not name:
        raise ValueError(
            "strict request bounds require an explicit supported API provider"
        )
    if stream or tools or output_schema is not None:
        raise ValueError(
            "strict request bounds support only nonstreaming text without tools/schema"
        )
    if any(
        m.files or m.call_id or m.metadata or not isinstance(m.content, str)
        for m in messages
    ):
        raise ValueError(
            "strict request bounds do not support attachments or native message metadata"
        )
    assert max_input_bytes is not None
    if sum(len(m.content.encode("utf-8")) for m in messages) > max_input_bytes:
        raise ValueError("input byte limit exceeded")


@contextmanager
def request_limits(
    model: str, max_input_bytes: int | None, max_provider_requests: int | None
) -> Iterator[None]:
    """Scope one reply's request allowance; never leak it to the next reply."""
    if max_input_bytes is None and max_provider_requests is None:
        yield
        return
    assert max_input_bytes is not None and max_provider_requests is not None
    if _limits.get() is not None:
        raise ValueError("nested strict request budgets are unsupported")
    token = _limits.set(
        _RequestLimits(model.split("/", 1)[0], max_input_bytes, max_provider_requests)
    )
    try:
        yield
    finally:
        _limits.reset(token)


def bounded_attempts(attempts: int) -> int:
    """Avoid retry backoff once a strict call's request allowance is spent."""
    limit = _limits.get()
    return min(attempts, limit.requests) if limit is not None else attempts


def _validate_client(client: Any, provider: str, limit: _RequestLimits) -> None:
    if (
        provider != limit.provider
        or str(client.base_url).rstrip("/") != _ENDPOINTS[limit.provider]
    ):
        raise ValueError("unsupported endpoint for strict request bounds")
    if type(client.max_retries) is not int or client.max_retries != 0:
        raise ValueError("strict request bounds require SDK retries disabled")


@contextmanager
def bounded_client(client: Any, provider: str) -> Iterator[Any]:
    """Give bounded sends an owned redirect-free transport without shared mutation.

    SDK defaults follow redirects, which can resend a POST outside our allowance.
    Safe injected clients are reused. Otherwise with_options preserves credentials
    and SDK settings while the newly allocated transport has an independent lifetime.
    """
    from .recovery import recovery_active

    limit = _limits.get()
    if limit is None and not recovery_active():
        yield client
        return
    # OpenAI init stores a lazy proxy whose _client is the SDK client, not
    # the HTTP transport. Unwrap it before inspecting SDK transport state.
    from .llm_openai import _LazyClient

    if isinstance(client, _LazyClient):
        client = client._materialize()
    if limit is not None:
        _validate_client(client, provider, limit)
    if client._client.follow_redirects is False:
        yield client
        return
    transport: Any
    if provider == "anthropic":
        from anthropic import DefaultHttpxClient as AnthropicHttpClient

        transport = AnthropicHttpClient(follow_redirects=False, timeout=client.timeout)
    else:
        from openai import DefaultHttpxClient as OpenAIHttpClient

        transport = OpenAIHttpClient(follow_redirects=False, timeout=client.timeout)
    try:
        yield client.with_options(http_client=transport)
    finally:
        transport.close()


def reserve_request(
    client: Any, provider: str, inputs: Any, *, relaxed_parameters: bool = False
) -> None:
    """Validate the concrete SDK route/input and debit immediately before send."""
    limit = _limits.get()
    if limit is None:
        return
    _validate_client(client, provider, limit)
    if client._client.follow_redirects is not False:
        raise ValueError("strict request bounds require HTTP redirects disabled")
    if relaxed_parameters:
        raise ValueError("strict request bounds require parameter support")
    serialized = json.dumps(inputs, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(serialized) > limit.input_bytes:
        raise ValueError("serialized input byte limit exceeded")
    if limit.used >= limit.requests:
        raise ValueError("provider request budget exhausted")
    limit.used += 1
