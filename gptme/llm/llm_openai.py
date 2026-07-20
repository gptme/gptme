from __future__ import annotations

import importlib
import json
import logging
import os
import re
from collections.abc import Generator, Iterable
from functools import lru_cache, wraps
from typing import TYPE_CHECKING, Any, cast

import requests

from ..config import Config, get_config
from ..constants import OPENAI_VERBOSITY, TEMPERATURE, TOP_P
from ..message import Message, MessageMetadata, UsageData, msgs2dicts
from ..telemetry import _calculate_llm_cost, record_llm_request
from .constants import _MIN_RESPONSE_TOKENS, OPENROUTER_APP_HEADERS
from .models import (
    CustomProvider,
    ModelMeta,
    Provider,
    is_custom_provider,
)
from .openai_responses import (
    ContentPart,
    MessageContent,
    MessageDict,
    ToolCall,
    ToolCallFunction,
    _content_to_responses_input,  # noqa: F401
    _extract_usage_token_counts,
    _messages_dicts_to_responses_input,
    _obj_get,
    _stream_responses_events,
    _tool_spec_to_responses_tool,
)
from .retry_abort import backoff_wait, current_generation
from .utils import (
    apply_cache_control,
    extract_tool_uses_from_assistant_message,
    parameters2dict,
    process_image_file,
)

if TYPE_CHECKING:
    # noreorder
    from openai import NotGiven, OpenAI  # fmt: skip
    from openai.types.chat import ChatCompletionToolParam  # fmt: skip

    from ..tools import ToolSpec  # fmt: skip


class _UnsetTimeout:
    """Sentinel for "no explicit timeout", without importing the openai SDK.

    Translated to ``openai.NOT_GIVEN`` when the real client is constructed.
    """


_UNSET_TIMEOUT = _UnsetTimeout()


class _LazyClient:
    """Records client parameters; constructs the real SDK client on first use.

    Importing the openai SDK takes ~1s. ``init()`` runs at startup for every
    session to fail fast on missing keys, but command-only invocations (e.g.
    ``gptme '/exit'``) never make an LLM call — deferring construction to the
    first attribute access keeps the SDK import off the startup path.
    """

    def __init__(self, cls_name: str, kwargs: dict[str, Any]):
        self._cls_name = cls_name
        self._kwargs = kwargs
        self._client: Any = None

    def _materialize(self) -> Any:
        if self._client is None:
            import openai

            kwargs = dict(self._kwargs)
            if isinstance(kwargs.get("timeout"), _UnsetTimeout):
                kwargs["timeout"] = openai.NOT_GIVEN
            self._client = getattr(openai, self._cls_name)(**kwargs)
        return self._client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._materialize(), name)


def _lazy_client_factory(cls_name: str):
    """Return an ``OpenAI``/``AzureOpenAI``-like constructor that defers SDK import."""

    def factory(**kwargs: Any) -> OpenAI:
        return cast("OpenAI", _LazyClient(cls_name, kwargs))

    return factory


# Dictionary to store clients for each provider (includes custom providers)
clients: dict[Provider, OpenAI] = {}
logger = logging.getLogger(__name__)

__all__ = [
    "ContentPart",
    "MessageContent",
]


def _get_provider_api_key(config: Config, provider: Provider, env_var: str) -> str:
    """Resolve a provider key from env/config first, then credentials.toml."""
    from ..credentials import get_stored_api_key

    api_key = config.get_env(env_var) or get_stored_api_key(str(provider))
    if api_key:
        return api_key
    raise KeyError(
        f"Environment variable {env_var} not set in env/config or credentials.toml"
    )


# OPENROUTER_APP_HEADERS canonical definition is in gptme.llm.constants;
# re-exported here for backward compatibility with existing
# `from gptme.llm.llm_openai import OPENROUTER_APP_HEADERS` imports.


def _get_temperature(
    provider: Provider,
    model_meta: ModelMeta | None = None,
    temperature: float | None = None,
) -> float:
    """Return the temperature for a given provider/model.

    Moonshot/Kimi models require temperature=1.
    OpenAI GPT-5 class models require temperature=1.
    All others use the caller value or global default.
    """
    if provider == "moonshot":
        return 1.0
    if model_meta is not None and "gpt-5" in model_meta.model:
        return 1.0
    return temperature if temperature is not None else TEMPERATURE


def _get_top_p(
    provider: Provider,
    model_meta: ModelMeta | None = None,
    top_p: float | None = None,
) -> float | None:
    """Return the top_p for a given provider.

    Moonshot/Kimi models require top_p=0.95.
    All others use the caller value or global default.
    Returns None for models that don't support top_p (e.g., gpt-5.x).
    """
    if model_meta is not None and "gpt-5" in model_meta.model:
        return None
    if provider == "moonshot":
        return 0.95
    return top_p if top_p is not None else TOP_P


def _record_usage(usage, model: str) -> MessageMetadata | None:
    """Record usage metrics as telemetry and return MessageMetadata."""
    if not usage:
        return None

    counts = _extract_usage_token_counts(usage)
    input_tokens = counts.input_tokens
    output_tokens = counts.output_tokens
    cache_read_tokens = counts.cache_read_tokens
    cache_creation_tokens = counts.cache_creation_tokens
    total_tokens = counts.total_tokens

    # Determine the provider for telemetry
    # For OpenRouter models, detect the underlying provider from the model string
    # e.g., "openrouter/anthropic/claude-sonnet-4.5" -> "anthropic"
    # gptme cloud models embed the real backend (gptme/openai/gpt-5,
    # gptme/openrouter/...), so strip the gptme/ prefix before detecting.
    provider = "openai"
    detect = model.removeprefix("gptme/")
    if detect.startswith("openrouter/"):
        parts = detect.split("/")
        if len(parts) >= 2:
            # openrouter/anthropic/... -> anthropic
            provider = parts[1]
    elif detect.startswith("anthropic/"):
        provider = "anthropic"

    # Record the LLM request with token usage
    record_llm_request(
        provider=provider,
        model=model,
        success=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        total_tokens=total_tokens,
    )

    # Calculate cost for metadata

    cost = _calculate_llm_cost(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
    )

    # Build nested usage data
    usage_data: UsageData = {}
    if input_tokens is not None:
        usage_data["input_tokens"] = input_tokens
    if output_tokens is not None:
        usage_data["output_tokens"] = output_tokens
    if cache_read_tokens is not None:
        usage_data["cache_read_tokens"] = cache_read_tokens
    if cache_creation_tokens is not None:
        usage_data["cache_creation_tokens"] = cache_creation_tokens

    # Return MessageMetadata for attachment to Message
    metadata: MessageMetadata = {"model": model}
    if usage_data:
        metadata["usage"] = usage_data
    if cost > 0:
        metadata["cost"] = cost
    return metadata


ALLOWED_FILE_EXTS = ["jpg", "jpeg", "png", "gif", "webp"]


def _make_response_format(output_schema):
    """Convert a Pydantic schema to OpenAI response_format.

    Args:
        output_schema: Optional Pydantic BaseModel class

    Returns:
        OpenAI response_format dict or None if no schema
    """

    if output_schema is None:
        return None

    # Get the JSON schema from Pydantic model
    json_schema = output_schema.model_json_schema()

    # Extract schema name from model
    schema_name = output_schema.__name__

    return {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": json_schema, "strict": True},
    }


def _responses_api_enabled() -> bool:
    """Check whether the Responses API is enabled.

    Enabled by default for models that support it (gpt-5 class, o-series).
    Set GPTME_OPENAI_RESPONSES_API=0 to force Chat Completions for debugging.
    """
    value = os.environ.get("GPTME_OPENAI_RESPONSES_API")
    if value is None:
        return True  # default-on for supported models
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _should_use_responses_api(
    provider: Provider, model_meta: ModelMeta, client: OpenAI
) -> bool:
    return (
        provider == "openai"
        and model_meta.supports_responses_api
        and not _is_proxy(client)
        and _responses_api_enabled()
    )


_VALID_VERBOSITY = {"low", "medium", "high"}
_verbosity_warned = False  # warn at most once per process lifetime


def _make_responses_text_config(
    output_schema, model_meta: ModelMeta
) -> dict[str, Any] | None:
    text_config: dict[str, Any] = {}

    if output_schema is not None:
        text_config["format"] = {
            "type": "json_schema",
            "name": output_schema.__name__,
            "schema": output_schema.model_json_schema(),
            "strict": True,
        }

    if OPENAI_VERBOSITY and model_meta.model.startswith("gpt-5"):
        global _verbosity_warned
        if OPENAI_VERBOSITY not in _VALID_VERBOSITY:
            if not _verbosity_warned:
                logger.warning(
                    "OPENAI_VERBOSITY=%r is not one of %s; ignoring.",
                    OPENAI_VERBOSITY,
                    sorted(_VALID_VERBOSITY),
                )
                _verbosity_warned = True
        else:
            text_config["verbosity"] = OPENAI_VERBOSITY

    return text_config or None


def _prepare_messages_for_responses_api(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
) -> tuple[str | None, list[dict[str, Any]], list[dict[str, Any]] | None]:
    from .models import get_model  # fmt: skip

    model_meta = get_model(model)
    messages_dicts: Iterable[dict[str, Any]] = _process_msgs(
        msgs2dicts(messages), model_meta
    )

    if tools:
        messages_dicts = _merge_tool_results_with_same_call_id(
            _handle_tools(messages_dicts)
        )

    typed_messages = _transform_msgs_for_special_provider(messages_dicts, model_meta)
    messages_list = list(typed_messages)
    instructions, input_items = _messages_dicts_to_responses_input(messages_list)
    responses_tools = (
        [_tool_spec_to_responses_tool(tool) for tool in tools] if tools else None
    )
    return instructions, input_items, responses_tools


def _extract_responses_reasoning(item: Any) -> str:
    """Extract reasoning text from a Responses API ``reasoning`` output item.

    Prefers the ``summary`` over the raw ``content``. Returns an empty string
    when no reasoning text is present. The caller logs it and embeds it in the
    response as a ``<think>`` block so the non-streaming path is consistent with
    ``stream()`` and the Anthropic provider, which both preserve reasoning.
    """
    summary = _obj_get(item, "summary") or []
    summary_text = "\n".join(
        part.text if hasattr(part, "text") else part.get("text", "") for part in summary
    ).strip()
    if summary_text:
        return summary_text

    content = _obj_get(item, "content") or []
    content_text = "\n".join(
        part.text if hasattr(part, "text") else part.get("text", "") for part in content
    ).strip()
    return content_text


def _init_openai_client(
    provider: Provider,
    api_key: str,
    base_url: str | None = None,
    timeout: float | _UnsetTimeout | NotGiven | None = None,
    default_headers: dict[str, str] | None = None,
) -> None:
    """Internal helper to create and register an OpenAI client for a provider.

    Handles Azure vs regular OpenAI client selection automatically.
    """
    from ..config import get_config  # fmt: skip

    # Shadow the SDK classes with lazy factories: constructing the real client
    # imports the openai SDK (~1s); defer that to first use (see _LazyClient).
    OpenAI = _lazy_client_factory("OpenAI")
    AzureOpenAI = _lazy_client_factory("AzureOpenAI")

    config = get_config()

    if timeout is None:
        timeout_str = config.get_env("LLM_API_TIMEOUT")
        try:
            timeout = float(timeout_str) if timeout_str else _UNSET_TIMEOUT
        except ValueError as parse_err:
            raise ValueError(
                f"Invalid LLM_API_TIMEOUT value: {timeout_str!r}. Must be a valid number."
            ) from parse_err

    if provider == "azure":
        azure_endpoint = base_url or config.get_env_required("AZURE_OPENAI_ENDPOINT")
        clients[provider] = AzureOpenAI(
            api_key=api_key,
            api_version="2023-07-01-preview",
            azure_endpoint=azure_endpoint,
            timeout=timeout,
        )
    else:
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=base_url or None,
            timeout=timeout,
            **({"default_headers": default_headers} if default_headers else {}),
        )


def reinit(provider: Provider, api_key: str) -> None:
    """Reinitialize an OpenAI-compatible provider with a new API key at runtime.

    This is the counterpart to ``init()`` for the ``/account`` command.
    Currently only supports providers backed by a plain ``OpenAI`` client
    (e.g. openai, openrouter, deepseek, groq, xai, gemini, custom providers).
    Azure is not supported for runtime reinit.
    """
    if provider not in clients:
        raise ValueError(
            f"Cannot reinit provider {provider!r}: not currently initialized"
        )
    if provider == "azure":
        raise ValueError("Runtime credential switch not supported for Azure")
    existing = clients[provider]
    if isinstance(existing, _LazyClient) and existing._client is None:
        # Not yet materialized: read the recorded param instead of triggering
        # the SDK import just to reinit.
        raw_base_url = existing._kwargs.get("base_url")
    else:
        raw_base_url = getattr(existing, "base_url", None)
    base_url = str(raw_base_url) if raw_base_url else None
    _init_openai_client(provider, api_key=api_key, base_url=base_url)


def init(provider: Provider, config: Config):
    """Initialize OpenAI client for a given provider.

    Missing API keys raise here (fail fast at startup); the SDK client itself
    is constructed lazily on first use (see _LazyClient).
    """
    OpenAI = _lazy_client_factory("OpenAI")
    AzureOpenAI = _lazy_client_factory("AzureOpenAI")

    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    proxy_url = config.get_env("LLM_PROXY_URL")

    # Set the proxy URL to the unified messages endpoint if not already set
    if proxy_url and not proxy_url.endswith("/messages"):
        proxy_url = proxy_url + "/messages"

    # Get configurable API timeout (default: client's own default of 10 minutes)
    # If not set explicitly via LLM_API_TIMEOUT, we use NOT_GIVEN to let the
    # client use its own default behavior, which may evolve with future versions.

    timeout_str = config.get_env("LLM_API_TIMEOUT")
    timeout: float | _UnsetTimeout
    try:
        timeout = float(timeout_str) if timeout_str else _UNSET_TIMEOUT
    except ValueError as parse_err:
        raise ValueError(
            f"Invalid LLM_API_TIMEOUT value: {timeout_str!r}. Must be a valid number."
        ) from parse_err

    if provider == "openai":
        api_key = proxy_key or _get_provider_api_key(config, provider, "OPENAI_API_KEY")
        _init_openai_client(
            provider, api_key=api_key, base_url=proxy_url or None, timeout=timeout
        )
    elif provider == "azure":
        api_key = config.get_env_required("AZURE_OPENAI_API_KEY")
        azure_endpoint = config.get_env_required("AZURE_OPENAI_ENDPOINT")
        clients[provider] = AzureOpenAI(
            api_key=api_key,
            api_version="2023-07-01-preview",
            azure_endpoint=azure_endpoint,
            timeout=timeout,
        )
    elif provider == "openrouter":
        api_key = proxy_key or _get_provider_api_key(
            config, provider, "OPENROUTER_API_KEY"
        )
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=proxy_url or "https://openrouter.ai/api/v1",
            timeout=timeout,
        )
    elif provider == "requesty":
        api_key = proxy_key or _get_provider_api_key(
            config, provider, "REQUESTY_API_KEY"
        )
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=proxy_url or "https://router.requesty.ai/v1",
            timeout=timeout,
        )
    elif provider == "gptme":
        from .llm_gptme import get_api_key, get_base_url

        api_key = proxy_key or get_api_key(config)
        base_url = proxy_url or get_base_url(config)
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
    elif provider == "gemini":
        api_key = _get_provider_api_key(config, provider, "GEMINI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=timeout,
        )
    elif provider == "xai":
        api_key = _get_provider_api_key(config, provider, "XAI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.x.ai/v1", timeout=timeout
        )
    elif provider == "grok-subscription":
        # SuperGrok subscription: OAuth token from grok CLI or gptme auth flow.
        # Routes through the grok-build subscription proxy (cli-chat-proxy.grok.com)
        # which requires the api:access scope in the JWT — same endpoint used by
        # other SuperGrok harnesses (openclaw, pi, etc.).
        from .llm_grok_subscription import GROK_CLIENT_VERSION, GROK_PROXY_URL, get_auth

        auth = get_auth()
        _init_openai_client(
            provider,
            api_key=auth.access_token,
            base_url=GROK_PROXY_URL,
            default_headers={"x-grok-client-version": GROK_CLIENT_VERSION},
            timeout=timeout,
        )
        return  # _init_openai_client already assigned clients[provider]
    elif provider == "groq":
        api_key = _get_provider_api_key(config, provider, "GROQ_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.groq.com/openai/v1", timeout=timeout
        )
    elif provider == "deepseek":
        api_key = _get_provider_api_key(config, provider, "DEEPSEEK_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.deepseek.com/v1", timeout=timeout
        )
    elif provider == "moonshot":
        api_key = _get_provider_api_key(config, provider, "MOONSHOT_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url="https://api.moonshot.ai/v1",
            timeout=timeout,
        )
    elif provider == "nvidia":
        api_key = _get_provider_api_key(config, provider, "NVIDIA_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url="https://integrate.api.nvidia.com/v1",
            timeout=timeout,
        )
    elif provider == "local":
        # OPENAI_API_BASE renamed to OPENAI_BASE_URL: https://github.com/openai/openai-python/issues/745
        api_base = config.get_env("OPENAI_API_BASE")
        api_base = api_base or config.get_env("OPENAI_BASE_URL")
        if not api_base:
            raise KeyError("Missing environment variable OPENAI_BASE_URL")
        api_key = config.get_env("OPENAI_API_KEY") or "ollama"
        clients[provider] = OpenAI(api_key=api_key, base_url=api_base, timeout=timeout)
    else:
        # Check if this is a custom provider (config-file based)
        custom_provider = next(
            (p for p in config.user.providers if p.name == provider), None
        )
        if custom_provider:
            api_key = custom_provider.get_api_key(config)
            clients[provider] = OpenAI(
                api_key=api_key,
                base_url=custom_provider.base_url,
                timeout=timeout,
            )
        else:
            # Check if this is a plugin provider (entry-point based)
            from .provider_plugins import get_provider_plugin  # fmt: skip

            plugin = get_provider_plugin(str(provider))
            if plugin:
                api_key = config.get_env(plugin.api_key_env) or ""
                if not api_key:
                    raise KeyError(
                        f"Missing environment variable {plugin.api_key_env} "
                        f"required by provider plugin {plugin.name!r}"
                    )
                clients[provider] = OpenAI(
                    api_key=api_key,
                    base_url=plugin.base_url,
                    timeout=timeout,
                )
            else:
                raise ValueError(f"Unknown provider: {provider}")

    assert clients[provider], f"Provider {provider} not initialized"


def get_client(provider: Provider) -> OpenAI:
    """Get client for specific provider, initializing if needed."""
    if provider not in clients:
        init(provider, get_config())
    return clients[provider]


def has_client(provider: Provider) -> bool:
    """Return whether a client already exists without triggering lazy init."""
    return provider in clients


def _prep_o1(msgs: Iterable[Message]) -> Generator[Message, None, None]:
    # prepare messages for OpenAI O1, which doesn't support the system role
    # and requires the first message to be from the user
    for msg in msgs:
        # Don't convert system messages that have call_id (tool results)
        # as they need to be converted to tool messages by _handle_tools
        if msg.role == "system" and msg.call_id is None:
            msg = msg.replace(
                role="user", content=f"<system>\n{msg.content}\n</system>"
            )
        yield msg


def _merge_consecutive(msgs: Iterable[Message]) -> Generator[Message, None, None]:
    # if consecutive messages from same role, merge them
    last_message = None
    for msg in msgs:
        if last_message is None:
            last_message = msg
            continue

        if last_message.role == msg.role:
            last_message = last_message.concat(msg)
            continue
        else:
            yield last_message
            last_message = msg

    if last_message:
        yield last_message


def _prep_deepseek_reasoner(msgs: list[Message]) -> Generator[Message, None, None]:
    yield msgs[0]
    yield from _merge_consecutive(_prep_o1(msgs[1:]))


@lru_cache(maxsize=2)
def _is_proxy(client: OpenAI) -> bool:
    proxy_url = get_config().get_env("LLM_PROXY_URL")
    # If client has the proxy URL set, it is using the proxy
    if not proxy_url:
        return False
    # Normalize URLs for comparison (remove trailing slashes)
    return str(client.base_url).rstrip("/") == proxy_url.rstrip("/")


def _handle_openai_transient_error(
    e, attempt, max_retries, base_delay, generation=None
):
    """Handle OpenAI API transient errors with exponential backoff.

    Retries on:
    - 5xx server errors (500-599): Internal errors, bad gateway, service unavailable, etc.
    - 429 rate limit errors: Should back off and retry
    - Connection errors: Network issues, timeouts
    - httpx transport/protocol errors: Incomplete reads, server disconnects mid-stream
    """
    from openai import APIConnectionError, APIStatusError, RateLimitError  # fmt: skip

    try:
        httpx = importlib.import_module("httpx")
    except ImportError:
        httpx = None

    # Allow tests to override max_retries via environment variable
    # This breaks out of the retry loop early to prevent test timeouts
    test_max_retries_str = os.environ.get("GPTME_TEST_MAX_RETRIES")
    if test_max_retries_str:
        try:
            test_max_retries = int(test_max_retries_str)
        except ValueError as parse_err:
            raise ValueError(
                f"Invalid GPTME_TEST_MAX_RETRIES value: {test_max_retries_str!r}. "
                "Must be a valid integer."
            ) from parse_err
        if attempt >= test_max_retries - 1:
            logger.warning(
                f"Test max_retries={test_max_retries} reached (attempt {attempt + 1}), not retrying"
            )
            raise e

    # Check if this is a transient error we should retry
    should_retry = False

    if isinstance(e, RateLimitError):
        # 429 rate limit - should back off and retry
        should_retry = True
    elif isinstance(e, APIConnectionError):
        # Connection errors are transient
        should_retry = True
    elif httpx is not None and isinstance(e, httpx.NetworkError | httpx.ProtocolError):
        # httpx transport/protocol errors (server disconnects mid-stream, incomplete reads)
        should_retry = True
    elif isinstance(e, APIStatusError):
        # Retry on all 5xx server errors (transient)
        if 500 <= e.status_code < 600:
            should_retry = True
        else:
            # Check for known transient issues in error message, body, or string repr
            # This catches OpenRouter proxying Anthropic's "Overloaded" errors
            error_texts = []
            if hasattr(e, "message"):
                error_texts.append(str(e.message))
            if hasattr(e, "body") and e.body:
                # Body can be dict with 'error' key or other formats
                if isinstance(e.body, dict):
                    error_texts.append(str(e.body.get("error", "")))
                    error_texts.append(str(e.body.get("message", "")))
                error_texts.append(str(e.body))
            # Also check string representation as fallback
            error_texts.append(str(e))

            combined_error = " ".join(error_texts).lower()
            if any(
                keyword in combined_error
                for keyword in ["overload", "internal", "timeout"]
            ):
                should_retry = True
            elif e.status_code == 402:
                # 402 Payment Required is non-transient (we never retry it),
                # but the bare APIStatusError is opaque. Surface an actionable
                # diagnostic when the body identifies a credits/budget cause.
                # A generic 402 (e.g. "Unauthorized") gets no diagnostic so we
                # don't mislabel auth failures as out-of-credits.
                if any(
                    hint in combined_error
                    for hint in ("affordable", "more credits", "fewer max_tokens")
                ):
                    # OpenRouter reserves model.max_output + reasoning_budget
                    # worth of credits when max_tokens is omitted. A partially-
                    # spent key then rejects requests as 402 even with plenty of
                    # room for the actual response. Eval callers silently swallow
                    # the resulting APIStatusError as gen_stderr and write empty
                    # conversation.jsonl, so surface guidance before re-raising.
                    # See: https://github.com/gptme/gptme/issues/2383
                    logger.warning(
                        "OpenAI/OpenRouter 402 insufficient credits — the request "
                        "reservation exceeds available key budget. Lower the "
                        "reservation by setting --max-tokens (or GPTME_MAX_TOKENS) "
                        "to a value such as 16000, or top up / switch the key. "
                        "Detail: %s",
                        combined_error[:500],
                    )
                elif any(
                    hint in combined_error
                    for hint in (
                        "insufficient credit",
                        "out of credit",
                        "no credits",
                        "insufficient_quota",
                        "insufficient funds",
                        "insufficient balance",
                        "credit balance",
                    )
                ):
                    # Generic out-of-credits / payment-required from a provider
                    # or gateway (e.g. the gptme.ai LLM gateway). Non-transient:
                    # the account/key needs a top-up or switch, retrying will not
                    # help. See: https://github.com/gptme/gptme-cloud/issues/61
                    logger.warning(
                        "402 payment required — the provider or gateway reports "
                        "the account is out of credits. Top up or switch the API "
                        "key / account; gptme will not retry. Detail: %s",
                        combined_error[:500],
                    )

    # Re-raise if not transient or max retries reached
    if not should_retry or attempt == max_retries - 1:
        raise e

    delay = base_delay * (2**attempt)
    status_code = getattr(e, "status_code", "unknown")
    logger.warning(
        f"OpenAI API transient error (status {status_code}), "
        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
    )
    if backoff_wait(delay, generation):
        logger.warning("Retry backoff aborted, re-raising original error")
        raise e


def retry_on_openai_error(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry functions on OpenAI API transient errors with exponential backoff.

    Handles 5xx server errors, rate limits, and other transient API issues.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Capture the retry generation once per call: if test teardown
            # interrupts pending retries after this point, every backoff wait
            # for this call aborts immediately (even attempts started later).
            generation = current_generation()
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    _handle_openai_transient_error(
                        e, attempt, max_retries, base_delay, generation=generation
                    )
            # _handle_openai_transient_error raises on last attempt,
            # but guard against silent None return if logic changes
            raise RuntimeError("retry exhausted without raising")  # pragma: no cover

        return wrapper

    return decorator


def retry_generator_on_openai_error(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry generator functions on OpenAI API transient errors with exponential backoff.

    Handles 5xx server errors, rate limits, and other transient API issues.

    Note: Retries only happen if no content has been yielded yet. Once streaming
    has started, errors are raised immediately to prevent duplicate output.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Capture the retry generation once per call (see retry_abort.py).
            generation = current_generation()
            for attempt in range(max_retries):
                has_yielded = False
                try:
                    gen = func(*args, **kwargs)
                    while True:
                        try:
                            value = next(gen)
                        except StopIteration as e:
                            # Generator finished normally, return its value
                            return e.value
                        # Mark as yielded BEFORE yielding to consumer
                        # This ensures we don't retry if consumer throws
                        has_yielded = True
                        yield value
                except Exception as e:
                    if has_yielded:
                        # Can't retry after streaming has started - would cause duplicates
                        raise
                    _handle_openai_transient_error(
                        e, attempt, max_retries, base_delay, generation=generation
                    )
            # _handle_openai_transient_error raises on last attempt,
            # but guard against silent None return if logic changes
            raise RuntimeError("retry exhausted without raising")  # pragma: no cover

        return wrapper

    return decorator


def _maybe_apply_verbosity(body: dict[str, Any], model_meta: ModelMeta) -> None:
    """Add verbosity request-body field for GPT-5+ models when set.

    GPT-5 family models support a `verbosity` parameter to control output length.
    Unset or invalid values result in the parameter being omitted (OpenAI default
    of "medium" applies).
    """
    global _verbosity_warned
    if not OPENAI_VERBOSITY:
        return
    if not model_meta.model.startswith("gpt-5"):
        return
    if OPENAI_VERBOSITY not in _VALID_VERBOSITY:
        if not _verbosity_warned:
            logger.warning(
                "OPENAI_VERBOSITY=%r is not one of %s; ignoring.",
                OPENAI_VERBOSITY,
                sorted(_VALID_VERBOSITY),
            )
            _verbosity_warned = True
        return
    body["verbosity"] = OPENAI_VERBOSITY


def _gptme_api_model(
    provider: Provider,
    model_meta: ModelMeta,
    is_proxy: bool,
    model: str,
    base_model: str,
) -> str:
    """Model name to send on the wire.

    For gptme cloud models, send the backend-prefixed id (e.g. "openai/gpt-5",
    "openrouter/openai/gpt-5.4") so the gateway routes to the right backend.
    Otherwise keep the existing behaviour: provider-prefixed under LLM_PROXY,
    bare model name otherwise.
    """
    if provider == "gptme" and "/" in model_meta.model:
        return model_meta.model
    return model if is_proxy else base_model


# OpenAI's reasoning-era models reject `max_tokens` and require
# `max_completion_tokens`: the o-series (o1, o3, o4, o5, ...) and gpt-5 and
# later (gpt-5..gpt-9, gpt-10+). Matched by family so new releases in these
# lines keep working without a code change; older models (gpt-4o, etc.) keep
# `max_tokens`.
_OPENAI_MAX_COMPLETION_RE = re.compile(r"^(?:o\d|gpt-(?:[5-9]|\d\d))")


def _max_tokens_param_name(provider: Provider, api_model: str) -> str:
    """Choose between ``max_tokens`` and ``max_completion_tokens``.

    Applies only when the request actually targets OpenAI's API: the native
    ``openai`` provider, or a gptme cloud model whose backend is openai.
    OpenRouter (including openrouter-backed gptme models) keeps ``max_tokens``.
    """
    targets_openai = provider == "openai" or (
        provider == "gptme" and api_model.startswith("openai/")
    )
    if targets_openai:
        bare = api_model.rsplit("/", 1)[-1].split("@")[0].lower()
        if _OPENAI_MAX_COMPLETION_RE.match(bare):
            return "max_completion_tokens"
    return "max_tokens"


@retry_on_openai_error()
def chat(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema=None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> tuple[str, MessageMetadata | None]:
    # This will generate code and such, so we need appropriate temperature and top_p params
    # top_p controls diversity, temperature controls randomness

    from . import _get_base_model, get_provider_from_model  # fmt: skip
    from .models import get_model  # fmt: skip

    provider = get_provider_from_model(model)
    client = get_client(provider)
    is_proxy = _is_proxy(client)

    base_model = _get_base_model(model)
    model_meta = get_model(model)
    is_reasoner = model_meta.supports_reasoning

    # make the model name prefix with the provider if using LLM_PROXY, to make proxy aware of the provider
    api_model = _gptme_api_model(provider, model_meta, is_proxy, model, base_model)

    if _should_use_responses_api(provider, model_meta, client):
        instructions, input_items, responses_tools = (
            _prepare_messages_for_responses_api(messages, model, tools)
        )
        text_config = _make_responses_text_config(output_schema, model_meta)

        response_kwargs: dict[str, Any] = {
            "model": api_model.split("@")[0],
            "input": input_items,
            "store": False,
        }
        if instructions is not None:
            response_kwargs["instructions"] = instructions
        if responses_tools is not None:
            response_kwargs["tools"] = responses_tools
        if text_config is not None:
            response_kwargs["text"] = text_config
        if max_tokens is not None:
            response_kwargs["max_output_tokens"] = max_tokens
        if not is_reasoner:
            response_kwargs["temperature"] = _get_temperature(
                provider, model_meta, temperature=temperature
            )
            top_p_value = _get_top_p(provider, model_meta, top_p=top_p)
            if top_p_value is not None:
                response_kwargs["top_p"] = top_p_value

        response = client.responses.create(**response_kwargs)
        metadata = _record_usage(response.usage, model)

        result: list[str] = []
        for item in response.output:
            item_type = _obj_get(item, "type")
            if item_type == "reasoning":
                if reasoning_text := _extract_responses_reasoning(item):
                    logger.debug("Reasoning content: %s", reasoning_text)
                    result.append(f"<think>\n{reasoning_text}\n</think>")
            elif item_type == "function_call":
                name = _obj_get(item, "name", "").strip()
                call_id = _obj_get(item, "call_id", "").strip()
                arguments = _obj_get(item, "arguments", "")
                result.append(f"@{name}({call_id}): {arguments}")
            elif item_type == "message":
                for part in _obj_get(item, "content", []) or []:
                    part_type = _obj_get(part, "type")
                    if part_type == "output_text":
                        result.append(_obj_get(part, "text", ""))
                    elif part_type == "refusal":
                        result.append(_obj_get(part, "refusal", ""))

        if not result:
            raise ValueError("Responses API returned no usable output items")
        return "\n".join(part for part in result if part), metadata

    from openai.types.chat import ChatCompletionMessageToolCall  # fmt: skip

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)
    response_format = _make_response_format(output_schema)

    # Build optional kwargs to avoid NOT_GIVEN/Omit type mismatch
    optional_kwargs: dict[str, Any] = {}
    if not is_reasoner:
        optional_kwargs["temperature"] = _get_temperature(
            provider, model_meta, temperature=temperature
        )
        top_p_value = _get_top_p(provider, model_meta, top_p=top_p)
        if top_p_value is not None:
            optional_kwargs["top_p"] = top_p_value
    if tools_dict:
        optional_kwargs["tools"] = tools_dict
    if response_format is not None:
        optional_kwargs["response_format"] = response_format
    if max_tokens is not None:
        optional_kwargs[_max_tokens_param_name(provider, api_model)] = max_tokens

    response = client.chat.completions.create(
        model=api_model.split("@")[0],
        messages=cast(list, messages_dicts),
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider, model_meta, max_tokens=max_tokens),
        **optional_kwargs,
    )
    metadata = _record_usage(response.usage, model)
    if not response.choices:
        raise ValueError("OpenAI API returned empty choices list")
    choice = response.choices[0]
    result = []
    if choice.finish_reason == "tool_calls":
        for tool_call in choice.message.tool_calls or []:
            assert isinstance(tool_call, ChatCompletionMessageToolCall)
            result.append(
                f"@{tool_call.function.name.strip()}({tool_call.id.strip()}): {tool_call.function.arguments}"
            )
    else:
        if reasoning_content := (
            getattr(choice.message, "reasoning_content", None)
            or getattr(choice.message, "reasoning", None)
        ):
            logger.debug("Reasoning content: %s", reasoning_content)
            result.append(f"<think>\n{reasoning_content}\n</think>\n")
        if choice.message.content:
            result.append(choice.message.content)

    if not result:
        raise ValueError(
            f"LLM returned empty response (finish_reason={choice.finish_reason})"
        )
    return "\n".join(result), metadata


def extra_headers(provider: Provider) -> dict[str, str]:
    """Return extra headers for the OpenAI API based on the model."""
    headers: dict[str, str] = {}
    if provider == "openrouter":
        # Shows in rankings on openrouter.ai
        headers |= OPENROUTER_APP_HEADERS
    return headers


_OPENROUTER_REASONING_DEFAULT = 20000
_VALID_QUANTIZATIONS = {"fp16", "bf16", "fp8", "int8", "int4", "unknown"}


def extra_body(
    provider: Provider, model_meta: ModelMeta, max_tokens: int | None = None
) -> dict[str, Any]:
    """Return extra body for the OpenAI API based on the model."""
    body: dict[str, Any] = {}
    _maybe_apply_verbosity(body, model_meta)
    if provider == "openrouter":
        # Enable detailed usage info including cached tokens
        # See: https://openrouter.ai/docs/guides/usage-accounting
        body["usage"] = {"include": True}
        if model_meta.supports_reasoning:
            reasoning_budget = _OPENROUTER_REASONING_DEFAULT
            if max_tokens is not None:
                available = max_tokens - _MIN_RESPONSE_TOKENS
                if available <= 0:
                    # Not enough room for reasoning AND a useful response
                    logger.warning(
                        "max_tokens=%d is too small to accommodate reasoning tokens "
                        "and a useful response (min %d tokens); "
                        "disabling OpenRouter extended reasoning.",
                        max_tokens,
                        _MIN_RESPONSE_TOKENS,
                    )
                    reasoning_budget = 0
                elif available < reasoning_budget:
                    logger.debug(
                        "max_tokens=%d cannot accommodate reasoning_budget=%d; "
                        "reducing to %d (reserving %d tokens for response).",
                        max_tokens,
                        reasoning_budget,
                        available,
                        _MIN_RESPONSE_TOKENS,
                    )
                    reasoning_budget = available
            if reasoning_budget > 0:
                body["reasoning"] = {"enabled": True, "max_tokens": reasoning_budget}
        # Provider routing preferences
        # See: https://openrouter.ai/docs/provider-routing
        provider_prefs: dict[str, Any] = {}

        if "@" in model_meta.model:
            provider_override = model_meta.model.split("@")[1]
            provider_prefs["order"] = [provider_override]
            provider_prefs["allow_fallbacks"] = False

        # Ensure routed provider supports all request parameters (tools,
        # response_format, etc.) — prevents silent failures when OpenRouter
        # falls back to a provider that doesn't support function calling.
        # NOTE: only set when reasoning is NOT enabled, because the
        # combination of require_parameters=True + reasoning extension can
        # eliminate all available providers (the reasoning body parameter
        # is not universally supported).
        if "reasoning" not in body:
            provider_prefs["require_parameters"] = True

        # Privacy: default to "deny" for non-reasoning models to preserve
        # user privacy. For reasoning models, skip the default — the triple
        # constraint (require_parameters + reasoning + data_collection="deny")
        # eliminates all available providers and causes 400 errors.
        if "reasoning" not in body:
            data_collection = get_config().get_env("OPENROUTER_DATA_COLLECTION", "deny")
        else:
            data_collection = get_config().get_env("OPENROUTER_DATA_COLLECTION")
        if data_collection:
            provider_prefs["data_collection"] = data_collection

        # Quantization preferences: restrict to specific precision levels.
        # Useful for controlling quality (fp16) or cost (int4/int8).
        # Set via OPENROUTER_QUANTIZATION env var as comma-separated values.
        # See: https://openrouter.ai/docs/provider-routing#quantization
        quantization = get_config().get_env("OPENROUTER_QUANTIZATION")
        if quantization:
            parsed = [q.strip() for q in quantization.split(",") if q.strip()]
            if parsed:
                invalid = [q for q in parsed if q not in _VALID_QUANTIZATIONS]
                if invalid:
                    logger.warning(
                        "Unknown OPENROUTER_QUANTIZATION value(s): %s. Valid values are: %s",
                        ", ".join(invalid),
                        ", ".join(sorted(_VALID_QUANTIZATIONS)),
                    )
                provider_prefs["quantizations"] = parsed

        body["provider"] = provider_prefs
    return body


def _stream_responses(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    model_meta: ModelMeta,
    output_schema=None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> Generator[str, None, MessageMetadata | None]:
    """Stream via the OpenAI Responses API (used for GPT-5 class models).

    Convergence note: If the reasoning/wrapping logic here and in
    llm_openai_subscription.py grow further apart, consider merging the
    streaming loop into a shared _stream_responses_events() helper that
    accepts an event iterator.  Both providers use the same Responses API
    SSE event types (output_text.delta, reasoning_text.delta,
    function_call_arguments.delta, output_item.added, completed).  The
    only differences are auth, base URL, and the request body shape —
    all of which are upstream of the event loop.
    """
    from . import _get_base_model, get_provider_from_model  # fmt: skip

    provider = get_provider_from_model(model)
    client = get_client(provider)
    is_proxy = _is_proxy(client)
    base_model = _get_base_model(model)
    api_model = model if is_proxy else base_model
    is_reasoner = model_meta.supports_reasoning

    instructions, input_items, responses_tools = _prepare_messages_for_responses_api(
        messages, model, tools
    )
    text_config = _make_responses_text_config(output_schema, model_meta)

    kwargs: dict[str, Any] = {
        "model": api_model.split("@")[0],
        "input": input_items,
        "store": False,
        "stream": True,
    }
    if instructions is not None:
        kwargs["instructions"] = instructions
    if responses_tools is not None:
        kwargs["tools"] = responses_tools
    if text_config is not None:
        kwargs["text"] = text_config
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens
    if not is_reasoner:
        kwargs["temperature"] = _get_temperature(
            provider, model_meta, temperature=temperature
        )
        top_p_value = _get_top_p(provider, model_meta, top_p=top_p)
        if top_p_value is not None:
            kwargs["top_p"] = top_p_value

    captured_metadata: MessageMetadata | None = None

    def _capture_usage(usage: Any) -> None:
        nonlocal captured_metadata
        captured_metadata = _record_usage(usage, model)

    stream = client.responses.create(**kwargs)
    yield from _stream_responses_events(stream, usage_callback=_capture_usage)

    return captured_metadata


@retry_generator_on_openai_error()
def stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema=None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> Generator[str, None, MessageMetadata | None]:
    from . import _get_base_model, get_provider_from_model  # fmt: skip
    from .models import get_model  # fmt: skip

    # Variable to capture metadata from usage recording
    captured_metadata: MessageMetadata | None = None

    provider = get_provider_from_model(model)
    client = get_client(provider)
    is_proxy = _is_proxy(client)

    base_model = _get_base_model(model)
    model_meta = get_model(model)
    is_reasoner = model_meta.supports_reasoning

    # make the model name prefix with the provider if using LLM_PROXY, to make proxy aware of the provider
    api_model = _gptme_api_model(provider, model_meta, is_proxy, model, base_model)

    # Dispatch to Responses API streaming when enabled for GPT-5 class models
    if _should_use_responses_api(provider, model_meta, client):
        captured_metadata = yield from _stream_responses(
            messages,
            model,
            tools,
            model_meta,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return captured_metadata

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)
    response_format = _make_response_format(output_schema)
    in_reasoning_block = False
    stop_reason = None

    # Build optional kwargs to avoid NOT_GIVEN/Omit type mismatch
    optional_kwargs: dict[str, Any] = {}
    if not is_reasoner:
        optional_kwargs["temperature"] = _get_temperature(
            provider, model_meta, temperature=temperature
        )
        top_p_value = _get_top_p(provider, model_meta, top_p=top_p)
        if top_p_value is not None:
            optional_kwargs["top_p"] = top_p_value
    if tools_dict:
        optional_kwargs["tools"] = tools_dict
    if response_format is not None:
        optional_kwargs["response_format"] = response_format
    if max_tokens is not None:
        optional_kwargs[_max_tokens_param_name(provider, api_model)] = max_tokens

    for chunk_raw in client.chat.completions.create(
        model=api_model.split("@")[0],
        messages=cast(list, messages_dicts),
        stream=True,
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider, model_meta, max_tokens=max_tokens),
        stream_options={"include_usage": True},
        **optional_kwargs,
    ):
        from openai.types.chat import ChatCompletionChunk  # fmt: skip
        from openai.types.chat.chat_completion_chunk import (  # fmt: skip
            ChoiceDeltaToolCall,
            ChoiceDeltaToolCallFunction,
        )

        # Cast the chunk to the correct type
        chunk = cast(ChatCompletionChunk, chunk_raw)

        # Record usage if available (typically in final chunk)
        # and capture metadata for message attachment
        if hasattr(chunk, "usage") and chunk.usage:
            captured_metadata = _record_usage(chunk.usage, model)

        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        stop_reason = choice.finish_reason
        delta = choice.delta

        # Handle reasoning content
        # OpenRouter API uses delta.reasoning
        # DeepSeek API uses delta.reasoning_content
        if reasoning_content := (
            getattr(delta, "reasoning_content", None)
            or getattr(delta, "reasoning", None)
        ):
            if not in_reasoning_block:
                yield "<think>\n"
                in_reasoning_block = True
            yield reasoning_content
        elif in_reasoning_block:
            yield "\n</think>\n\n"
            in_reasoning_block = False
            if delta.content is not None:
                yield delta.content
        elif delta.content is not None:
            yield delta.content

        # Handle tool calls
        if delta.tool_calls:
            for tool_call in delta.tool_calls:
                if isinstance(tool_call, ChoiceDeltaToolCall) and tool_call.function:
                    func = tool_call.function
                    if isinstance(func, ChoiceDeltaToolCallFunction):
                        if func.name:
                            yield f"\n@{func.name}({tool_call.id}): "
                        if func.arguments:
                            yield func.arguments

        # TODO: figure out how to get reasoning summary from OpenAI using Chat Completions API
        # if delta.type == "response.reasoning_summary.delta":
        #     if not in_reasoning_block:
        #         yield "<think>\n"
        #         in_reasoning_block = True
        #     yield delta.text

    if in_reasoning_block:
        yield "\n</think>\n"

    logger.debug(f"Stop reason: {stop_reason}")

    # Return the captured metadata (accessible via StopIteration.value)
    return captured_metadata


def _handle_tools(message_dicts: Iterable[dict]) -> Generator[dict, None, None]:
    for message in message_dicts:
        # Format tool result as expected by the model
        if message["role"] == "system" and "call_id" in message:
            # Convert system+call_id to tool message (conforms to MessageDict)
            modified_message = dict(message)
            modified_message["role"] = "tool"
            modified_message["tool_call_id"] = modified_message.pop("call_id")
            yield modified_message
        # Find tool_use occurrences and format them as expected
        elif message["role"] == "assistant":
            modified_message = dict(message)

            content, tool_uses = extract_tool_uses_from_assistant_message(
                message["content"], tool_format_override="tool"
            )

            # Format tool uses for OpenAI API with explicit typing
            tool_calls: list[ToolCall] = [
                cast(
                    ToolCall,
                    {
                        "id": tooluse.call_id or "",
                        "type": "function",
                        "function": cast(
                            ToolCallFunction,
                            {
                                "name": tooluse.tool,
                                "arguments": json.dumps(tooluse.kwargs or {}),
                            },
                        ),
                    },
                )
                for tooluse in tool_uses
            ]

            if content:
                modified_message["content"] = content

            if tool_calls:
                # Clean content property if empty otherwise the call fails
                if not content:
                    del modified_message["content"]
                modified_message["tool_calls"] = tool_calls

            yield modified_message
        else:
            yield message


def _merge_tool_results_with_same_call_id(
    messages_dicts: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    When we call a tool, this tool can potentially yield multiple messages. However
    the API expect to have only one tool result per tool call. This function tries
    to merge subsequent tool results with the same call ID as expected by
    the API.

    Note: Internally uses MessageDict casts for type clarity during processing.
    """
    messages_new: list[dict[str, Any]] = []

    for message in messages_dicts:
        if messages_new and (
            message["role"] == "tool"
            and messages_new[-1]["role"] == "tool"
            and message.get("tool_call_id") == messages_new[-1].get("tool_call_id")
        ):
            prev_msg = messages_new[-1]
            prev_content = prev_msg.get("content")
            current_content = message.get("content")

            # Ensure both contents are lists of content parts
            if not isinstance(prev_content, list):
                prev_content = [{"type": "text", "text": prev_content or ""}]
            if not isinstance(current_content, list):
                current_content = [{"type": "text", "text": current_content or ""}]

            # Create merged message (conforms to MessageDict structure)
            messages_new[-1] = {
                "role": "tool",
                "content": prev_content + current_content,
                "tool_call_id": prev_msg.get("tool_call_id"),
            }
        else:
            messages_new.append(message)

    return messages_new


def _process_file(msg: dict[str, Any], model: ModelMeta) -> dict[str, Any]:
    """Process remaining file attachments (images only) for non-tool-response messages.

    Text files are already embedded by embed_attached_file_content() in
    prepare_messages(). Only non-text files (images, binaries) remain here.

    For tool response messages (call_id), use _process_msgs which handles
    image forwarding via follow-up user messages.

    Args:
        msg: A message dict (expected to conform to MessageDict structure)
        model: Model metadata for checking vision support

    Returns:
        The processed message dict with files converted to content parts.
        The returned dict conforms to MessageDict structure.
    """
    message_content = msg.get("content")

    # combines a content message with a list of files
    # Note: don't add empty text items — some providers (e.g. Z.AI/GLM) reject them
    content: list[dict[str, Any]] = (
        message_content
        if isinstance(message_content, list)
        else ([{"type": "text", "text": message_content}] if message_content else [])
    )

    has_images = False

    files = msg.pop("files", [])
    for f in files:

        def check_vision():
            return model.supports_vision

        result = process_image_file(
            f,
            content,
            max_size_mb=20,
            expand_user=False,
            check_vision_support=check_vision,
        )
        if result is None:
            content.append(
                {
                    "type": "text",
                    "text": f"[WARNING: Model doesn't support viewing file: {f}]",
                }
            )
            continue

        data, media_type = result
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"},
            }
        )
        has_images = True

    msg["content"] = content

    # Images must come from user with openai
    if msg["role"] == "system" and has_images:
        msg["role"] = "user"

    return msg  # Conforms to MessageDict structure


def _process_msgs(
    msgs: Iterable[dict[str, Any]], model: ModelMeta
) -> Iterable[dict[str, Any]]:
    """Process all message file attachments, routing by message type.

    For regular messages: delegates to _process_file to embed images in content.
    For tool response messages (call_id): processes text content normally, then
    forwards any images as a follow-up user message (since OpenAI tool messages
    only support text content, but the model may still benefit from seeing images
    produced by tools like screenshot).

    Args:
        msgs: Iterable of message dicts
        model: Model metadata for checking vision support

    Yields:
        Processed message dicts, with possible extra follow-up user messages.
    """
    for msg in msgs:
        if "call_id" in msg:
            # Extract files before yielding the tool response
            files = msg.pop("files", None) or []
            text = msg.get("content")
            if not isinstance(text, list):
                msg["content"] = [{"type": "text", "text": text}]
            yield msg

            # Forward any images as a follow-up user message
            if files and model.supports_vision:
                followup_content: list[dict[str, Any]] = []
                for f in files:
                    result = process_image_file(
                        f,
                        followup_content,
                        max_size_mb=20,
                        expand_user=False,
                        check_vision_support=lambda: model.supports_vision,
                    )
                    if result is not None:
                        data, media_type = result
                        followup_content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{data}"
                                },
                            }
                        )

                if followup_content:
                    yield {"role": "user", "content": followup_content}
        else:
            yield _process_file(msg, model)


def _transform_msgs_for_special_provider(
    messages_dicts: Iterable[dict[str, Any]], model: ModelMeta
) -> Iterable[MessageDict]:
    """Transform message dicts for provider-specific requirements.

    Args:
        messages_dicts: Iterable of message dicts (expected MessageDict structure)
        model: Model metadata for determining transformation rules

    Returns:
        Transformed message dicts conforming to MessageDict type
    """
    # groq and deepseek needs message.content to be a string
    if model.provider == "groq" or model.provider == "deepseek":
        result: list[MessageDict] = []
        for msg in messages_dicts:
            content = msg.get("content")
            # Handle messages without content (e.g., tool call messages)
            if content is None:
                # DeepSeek requires reasoning_content for assistant messages with tool_calls
                # Since we don't store reasoning_content in Message objects, add empty reasoning_content field
                if (
                    model.provider == "deepseek"
                    and msg.get("role") == "assistant"
                    and msg.get("tool_calls")
                ):
                    result.append(cast(MessageDict, {**msg, "reasoning_content": ""}))
                else:
                    result.append(cast(MessageDict, msg))
                continue
            # Handle list content (multi-modal messages)
            if isinstance(content, list):
                text_parts = [
                    part["text"]
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                # Use placeholder if all parts are non-text (e.g., images only)
                transformed = (
                    "\n\n".join(text_parts) if text_parts else "[non-text content]"
                )
                result.append(cast(MessageDict, {**msg, "content": transformed}))
            else:
                result.append(cast(MessageDict, msg))
        return result

    # OpenRouter reasoning models (e.g., Moonshot AI Kimi) need reasoning_content
    # for assistant messages with tool_calls when thinking mode is enabled
    # This prevents: "thinking is enabled but reasoning_content is missing in assistant tool call message"
    if model.provider == "openrouter" and model.supports_reasoning:

        def _extract_and_strip_reasoning(
            content: str | None,
        ) -> tuple[str, str]:
            """Extract reasoning from <think>/<thinking> tags and return cleaned content.

            Returns:
                Tuple of (reasoning_content, cleaned_content_without_think_tags)
            """
            if not content:
                return "", ""

            # Extract content from <think>...</think> and <thinking>...</thinking> blocks
            think_matches = re.findall(
                r"<think>(.*?)</think>", content, flags=re.DOTALL
            )
            thinking_matches = re.findall(
                r"<thinking>(.*?)</thinking>", content, flags=re.DOTALL
            )
            all_reasoning = think_matches + thinking_matches
            reasoning = (
                "\n".join(r.strip() for r in all_reasoning if r.strip())
                if all_reasoning
                else ""
            )

            # Remove <think> and <thinking> tags from content to prevent duplication
            cleaned_content = re.sub(
                r"<think>.*?</think>\s*", "", content, flags=re.DOTALL
            )
            cleaned_content = re.sub(
                r"<thinking>.*?</thinking>\s*", "", cleaned_content, flags=re.DOTALL
            )
            cleaned_content = cleaned_content.strip()

            return reasoning, cleaned_content

        openrouter_result: list[MessageDict] = []
        for msg in messages_dicts:
            if (
                msg.get("role") == "assistant"
                and msg.get("tool_calls")
                and "reasoning_content" not in msg
            ):
                # Extract reasoning and clean content to prevent context duplication
                content = msg.get("content", "")

                # Handle list content (multi-modal messages)
                if isinstance(content, list):
                    # Extract text from list items
                    text_parts = []
                    for item in content:
                        if isinstance(item, str):
                            text_parts.append(item)
                        elif isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    content = "\n".join(text_parts)

                reasoning, cleaned_content = _extract_and_strip_reasoning(content)
                result_msg: dict[str, Any] = {
                    **msg,
                    "reasoning_content": reasoning,
                }
                if cleaned_content:
                    result_msg["content"] = cleaned_content
                else:
                    # Remove empty content to avoid provider errors (e.g. Z.AI/GLM
                    # rejects messages with empty text content)
                    result_msg.pop("content", None)
                openrouter_result.append(cast(MessageDict, result_msg))
            else:
                openrouter_result.append(cast(MessageDict, msg))
        return openrouter_result

    # Return as-is with cast for type safety
    return cast(Iterable[MessageDict], messages_dicts)


def _spec2tool(spec: ToolSpec, model: ModelMeta) -> ChatCompletionToolParam:
    name = spec.name
    if spec.block_types:
        name = spec.block_types[0]

    description = spec.get_instructions("tool") or spec.desc or ""
    if len(description) > 1024:
        logger.warning(
            "Description for tool `%s` is too long ( %d > 1024 chars). Truncating...",
            spec.name,
            len(description),
        )
        description = description[:1024]

    # Custom providers are OpenAI-compatible and support tools API
    if model.provider in [
        "openai",
        "azure",
        "openrouter",
        "deepseek",
        "local",
    ] or is_custom_provider(model.model.split("/")[0]):
        all_required = all(p.required for p in spec.parameters)
        supports_strict = model.supports_strict_tools and all_required
        function_def: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": parameters2dict(spec.parameters),
        }
        if supports_strict:
            function_def["strict"] = True
        return cast(
            "ChatCompletionToolParam", {"type": "function", "function": function_def}
        )
    raise ValueError("Provider doesn't support tools API")


@lru_cache(maxsize=1)
def get_available_models(provider: Provider) -> list[ModelMeta]:
    """Get available models from a provider."""
    config = get_config()

    # Check for custom provider first (e.g., "ollama" with custom base_url)
    if is_custom_provider(provider):
        custom = next((p for p in config.user.providers if p.name == provider), None)
        if custom:
            return _get_openai_compatible_models(config, provider, custom.base_url)
        # Fall through to local if custom provider not found in config
        return _get_openai_compatible_models(config, provider)

    if provider == "local":
        return _get_openai_compatible_models(config, provider)

    if provider == "gptme":
        from .llm_gptme import get_api_key, get_models_url

        return _get_openai_compatible_models(
            config,
            provider,
            base_url=get_models_url(config),
            api_key=get_api_key(config),
        )

    if provider != "openrouter":
        raise ValueError(f"Provider {provider} does not support listing models")

    # Check if we should use the proxy
    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    proxy_url = config.get_env("LLM_PROXY_URL")

    if proxy_key and proxy_url:
        # Use proxy for models endpoint
        # Strip /messages from proxy URL and replace with /models
        api_key = proxy_key
        if proxy_url.endswith("/messages"):
            base_url = proxy_url.rsplit("/messages", 1)[0]
            url = f"{base_url}/models"
        else:
            # Fallback if URL structure is different
            url = f"{proxy_url.rstrip('/')}/models"
        headers = {"x-api-key": api_key}
    else:
        # Direct OpenRouter API call (fallback)
        api_key = config.get_env_required("OPENROUTER_API_KEY")
        url = "https://openrouter.ai/api/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Convert raw models to ModelMeta objects
        raw_models = data.get("data", [])
        return [openrouter_model_to_modelmeta(model) for model in raw_models]
    except requests.RequestException as e:
        logger.error(f"Failed to retrieve models from {provider}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error retrieving models from {provider}: {e}")
        raise


def _get_openai_compatible_models(
    config,
    provider_name: str = "local",
    base_url: str | None = None,
    api_key: str | None = None,
) -> list[ModelMeta]:
    """Get models from an OpenAI-compatible provider."""
    # Get base URL from parameter (custom provider) or env var (local provider)
    if base_url is None:
        base_url = config.get_env("OPENAI_BASE_URL", "http://localhost:11434/v1")

    # Ensure we're hitting the /models endpoint
    if base_url.endswith("/v1"):
        models_url = f"{base_url}/models"
    elif base_url.endswith("/v1/"):
        models_url = f"{base_url}models"
    else:
        # Try to construct a reasonable URL
        models_url = f"{base_url.rstrip('/')}/v1/models"

    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        response = requests.get(models_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        # OpenAI-compatible format: {"data": [...], "object": "list"}
        raw_models = data.get("data", [])
        return [
            _openai_compatible_model_to_modelmeta(model, provider_name)
            for model in raw_models
        ]
    except requests.RequestException as e:
        log_fn = logger.debug if provider_name == "local" else logger.warning
        log_fn(f"Failed to retrieve models from {provider_name} provider: {e}")
        # Return empty list instead of raising - local server might not be running
        return []
    except Exception as e:
        log_fn = logger.debug if provider_name == "local" else logger.warning
        log_fn(f"Unexpected error retrieving {provider_name} models: {e}")
        return []


def _openai_compatible_model_to_modelmeta(
    model_data: dict, provider_name: str
) -> ModelMeta:
    """Convert OpenAI-compatible model data to ModelMeta."""

    model_id = model_data.get("id", model_data.get("name", "unknown"))

    context = model_data.get("context_length", 128_000)

    # Use CustomProvider for non-builtin providers, "local" for local provider
    provider: Provider
    if provider_name == "local":
        provider = "local"
    elif provider_name == "gptme":
        provider = "gptme"
    else:
        provider = CustomProvider(provider_name)

    # Detect vision support from architecture/modality info if available
    architecture = model_data.get("architecture", {})
    input_modalities = architecture.get("input_modalities", [])
    supports_vision = "image" in input_modalities
    if not supports_vision and not input_modalities:
        modality = architecture.get("modality", "")
        input_side = modality.split("->")[0] if "->" in modality else ""
        supports_vision = "image" in input_side

    max_output = model_data.get("max_completion_tokens")

    return ModelMeta(
        provider=provider,
        model=model_id,
        context=context,
        max_output=max_output,
        supports_streaming=model_data.get("supports_streaming", True),
        supports_vision=supports_vision,
        supports_reasoning=model_data.get("supports_reasoning", False),
        price_input=0,  # pricing unknown for dynamically discovered models
        price_output=0,
    )


def openrouter_model_to_modelmeta(model_data: dict) -> ModelMeta:
    """Convert OpenRouter model data to ModelMeta object."""
    pricing = model_data.get("pricing", {})
    price_input = float(pricing.get("prompt", 0)) * 1_000_000
    price_output = float(pricing.get("completion", 0)) * 1_000_000
    # Check for vision support: look for "image" in input modalities
    # OpenRouter uses modalities like "text+image->text" (not "vision")
    architecture = model_data.get("architecture", {})
    input_modalities = architecture.get("input_modalities", [])
    vision = "image" in input_modalities
    if not vision and not input_modalities:
        # Fallback: parse input side of modality string (before "->")
        modality = architecture.get("modality", "")
        input_side = modality.split("->")[0] if "->" in modality else ""
        vision = "image" in input_side
    reasoning = "reasoning" in model_data.get("supported_parameters", [])
    include_reasoning = "include_reasoning" in model_data.get(
        "supported_parameters", []
    )

    # Strip redundant "openrouter/" prefix — the Supabase models proxy pre-prefixes
    # IDs (e.g. "openrouter/anthropic/claude-3-5-sonnet"), but ModelMeta.full adds
    # the provider prefix again, producing "openrouter/openrouter/...".
    model_id = model_data.get("id", "").removeprefix("openrouter/")

    return ModelMeta(
        provider="openrouter",
        model=model_id,
        context=model_data.get("context_length", 128_000),
        max_output=model_data.get("max_completion_tokens"),
        supports_streaming=True,  # Most OpenRouter models support streaming
        supports_vision=vision,
        supports_reasoning=reasoning and include_reasoning,
        price_input=price_input,
        price_output=price_output,
    )


def _prepare_messages_for_api(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
) -> tuple[list[MessageDict], list[ChatCompletionToolParam] | None]:
    """Prepare messages for API call, handling model-specific transformations.

    Returns:
        Tuple of (messages as MessageDict list, tools if provided)
    """
    from .models import get_model  # fmt: skip

    model_meta = get_model(model)

    # o1 models need _prep_o1 applied to ALL messages (including first), but no merging
    if any(
        model_meta.model.startswith(om) for om in ["o1", "o3", "o4"]
    ) or model_meta.model.startswith("gpt-5"):
        messages = list(_prep_o1(messages))
    # other reasoning models use deepseek reasoner prep (first message unchanged, then _prep_o1 on rest)
    elif (
        any(
            m in model_meta.model
            for m in [
                "deepseek-reasoner",
                "deepseek-chat",
                "kimi-k2",
                "magistral",
            ]
        )
        or model_meta.supports_reasoning
    ):
        messages = list(_prep_deepseek_reasoner(messages))

    # Process message dicts - _process_msgs handles both regular and tool response messages
    # For tool responses with images, it emits a follow-up user message
    messages_dicts: Iterable[dict[str, Any]] = _process_msgs(
        msgs2dicts(messages), model_meta
    )

    tools_dict = [_spec2tool(tool, model_meta) for tool in tools] if tools else None

    if tools_dict is not None:
        messages_dicts = _merge_tool_results_with_same_call_id(
            _handle_tools(messages_dicts)
        )

    # Transform returns MessageDict-typed messages
    typed_messages = _transform_msgs_for_special_provider(messages_dicts, model_meta)

    messages_list: list[MessageDict] = list(typed_messages)

    # Apply cache control for Anthropic models on OpenRouter
    if model.startswith("openrouter/anthropic/"):
        # apply_cache_control uses dict[Any, Any] internally, cast at boundary
        cache_result, _ = apply_cache_control(cast(list[dict[str, Any]], messages_list))
        messages_list = cast(list[MessageDict], cache_result)

    return messages_list, tools_dict
