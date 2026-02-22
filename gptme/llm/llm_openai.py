import json
import logging
import os
import re
import time
from collections.abc import Generator, Iterable
from functools import lru_cache, wraps
from typing import TYPE_CHECKING, Any, TypedDict, cast

import requests
from openai import NOT_GIVEN
from typing_extensions import NotRequired

from ..config import Config, get_config
from ..constants import TEMPERATURE, TOP_P
from ..message import Message, MessageMetadata, msgs2dicts
from ..telemetry import _calculate_llm_cost, record_llm_request
from ..tools import ToolSpec
from .models import (
    CustomProvider,
    ModelMeta,
    Provider,
    is_custom_provider,
)
from .utils import (
    apply_cache_control,
    extract_tool_uses_from_assistant_message,
    parameters2dict,
    process_image_file,
)

if TYPE_CHECKING:
    # noreorder
    from openai import OpenAI  # fmt: skip
    from openai.types.chat import ChatCompletionToolParam  # fmt: skip


# Dictionary to store clients for each provider (includes custom providers)
clients: dict[Provider, "OpenAI"] = {}
logger = logging.getLogger(__name__)


# Type definitions for message dictionaries used in API transformations
class ContentPart(TypedDict):
    """A content part in a multimodal message."""

    type: str  # "text", "image_url", etc. - always required
    text: NotRequired[str]  # For text parts
    image_url: NotRequired[dict[str, str]]  # For image parts: {"url": "data:..."}


# Type alias for message content (can be string, list of parts, or None)
MessageContent = str | list[ContentPart | str] | None


class ToolCallFunction(TypedDict):
    """Function details in a tool call."""

    name: str
    arguments: str


class ToolCall(TypedDict):
    """A tool call in an assistant message."""

    id: str
    type: str  # "function"
    function: ToolCallFunction


class MessageDict(TypedDict):
    """
    Dictionary representation of a chat message for API calls.

    This type covers the internal message format used when preparing
    messages for various LLM providers. Not all fields are present
    in all messages - the required fields vary by role.
    """

    # Core fields (always required)
    role: str  # "system", "user", "assistant", "tool"
    content: MessageContent

    # Tool-related fields (optional)
    tool_calls: NotRequired[list[ToolCall]]  # For assistant messages that call tools
    tool_call_id: NotRequired[str]  # For tool response messages
    call_id: NotRequired[
        str
    ]  # Legacy field for tool responses (converted to tool_call_id)

    # Reasoning/thinking fields (for models with thinking/reasoning support)
    reasoning_content: NotRequired[str]

    # Multimodal fields
    files: NotRequired[
        list[str]
    ]  # Image/file attachments as file paths (processed before API call)


# Shows in rankings on openrouter.ai
OPENROUTER_APP_HEADERS = {
    "HTTP-Referer": "https://github.com/gptme/gptme",
    "X-Title": "gptme",
}


def _record_usage(usage, model: str) -> MessageMetadata | None:
    """Record usage metrics as telemetry and return MessageMetadata."""
    if not usage:
        return None

    # Extract token counts (OpenAI uses different field names than Anthropic)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    output_tokens = getattr(usage, "completion_tokens", None)
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read_tokens = getattr(details, "cached_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)

    # subtract cache_read_tokens from prompt_tokens to avoid double counting
    # Ensure we have actual integers, not Mock objects from tests
    if isinstance(prompt_tokens, int):
        cache_tokens = cache_read_tokens if isinstance(cache_read_tokens, int) else 0
        input_tokens = prompt_tokens - cache_tokens
    else:
        input_tokens = None

    # Determine the provider for telemetry
    # For OpenRouter models, detect the underlying provider from the model string
    # e.g., "openrouter/anthropic/claude-sonnet-4.5" -> "anthropic"
    provider = "openai"
    if model.startswith("openrouter/"):
        parts = model.split("/")
        if len(parts) >= 2:
            # openrouter/anthropic/... -> anthropic
            provider = parts[1]

    # Record the LLM request with token usage
    record_llm_request(
        provider=provider,
        model=model,
        success=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        total_tokens=total_tokens,
    )

    # Calculate cost for metadata

    cost = _calculate_llm_cost(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
    )

    # Return MessageMetadata for attachment to Message
    metadata: MessageMetadata = {"model": model}
    if input_tokens is not None:
        metadata["input_tokens"] = input_tokens
    if output_tokens is not None:
        metadata["output_tokens"] = output_tokens
    if cache_read_tokens is not None:
        metadata["cache_read_tokens"] = cache_read_tokens
    if cost > 0:
        metadata["cost"] = cost
    return metadata


# TODO: improve provider routing for openrouter: https://openrouter.ai/docs/provider-routing
# TODO: set required-parameters: https://openrouter.ai/docs/provider-routing#required-parameters-_beta_
# TODO: set quantization: https://openrouter.ai/docs/provider-routing#quantization


ALLOWED_FILE_EXTS = ["jpg", "jpeg", "png", "gif"]


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


def init(provider: Provider, config: Config):
    """Initialize OpenAI client for a given provider."""
    from openai import AzureOpenAI, OpenAI  # fmt: skip

    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    proxy_url = config.get_env("LLM_PROXY_URL")

    # Set the proxy URL to the unified messages endpoint if not already set
    if proxy_url and not proxy_url.endswith("/messages"):
        proxy_url = proxy_url + "/messages" if proxy_url else None

    # Get configurable API timeout (default: client's own default of 10 minutes)
    # If not set explicitly via LLM_API_TIMEOUT, we use NOT_GIVEN to let the
    # client use its own default behavior, which may evolve with future versions.

    timeout_str = config.get_env("LLM_API_TIMEOUT")
    try:
        timeout = float(timeout_str) if timeout_str else NOT_GIVEN
    except ValueError as parse_err:
        raise ValueError(
            f"Invalid LLM_API_TIMEOUT value: {timeout_str!r}. Must be a valid number."
        ) from parse_err

    if provider == "openai":
        api_key = proxy_key or config.get_env_required("OPENAI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=proxy_url or None,
            timeout=timeout,
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
        api_key = proxy_key or config.get_env_required("OPENROUTER_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=proxy_url or "https://openrouter.ai/api/v1",
            timeout=timeout,
        )
    elif provider == "gemini":
        api_key = config.get_env_required("GEMINI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta",
            timeout=timeout,
        )
    elif provider == "xai":
        api_key = config.get_env_required("XAI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.x.ai/v1", timeout=timeout
        )
    elif provider == "groq":
        api_key = config.get_env_required("GROQ_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.groq.com/openai/v1", timeout=timeout
        )
    elif provider == "deepseek":
        api_key = config.get_env_required("DEEPSEEK_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.deepseek.com/v1", timeout=timeout
        )
    elif provider == "nvidia":
        api_key = config.get_env_required("NVIDIA_API_KEY")
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
        # Check if this is a custom provider
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
            raise ValueError(f"Unknown provider: {provider}")

    assert clients[provider], f"Provider {provider} not initialized"


def get_client(provider: Provider) -> "OpenAI":
    """Get client for specific provider, initializing if needed."""
    if provider not in clients:
        init(provider, get_config())
    return clients[provider]


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
            last_message = last_message.replace(
                content=f"{last_message.content}\n\n{msg.content}"
            )
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
def _is_proxy(client: "OpenAI") -> bool:
    proxy_url = get_config().get_env("LLM_PROXY_URL")
    # If client has the proxy URL set, it is using the proxy
    if not proxy_url:
        return False
    # Normalize URLs for comparison (remove trailing slashes)
    return str(client.base_url).rstrip("/") == proxy_url.rstrip("/")


def _handle_openai_transient_error(e, attempt, max_retries, base_delay):
    """Handle OpenAI API transient errors with exponential backoff.

    Retries on:
    - 5xx server errors (500-599): Internal errors, bad gateway, service unavailable, etc.
    - 429 rate limit errors: Should back off and retry
    - Connection errors: Network issues, timeouts
    - httpx transport/protocol errors: Incomplete reads, server disconnects mid-stream
    """
    from openai import APIConnectionError, APIStatusError, RateLimitError  # fmt: skip

    try:
        import httpx
    except ImportError:
        httpx = None  # type: ignore[assignment]

    # Allow tests to override max_retries via environment variable
    # This breaks out of the retry loop early to prevent test timeouts
    test_max_retries_str = os.environ.get("GPTME_TEST_MAX_RETRIES")
    if test_max_retries_str:
        test_max_retries = int(test_max_retries_str)
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

    # Re-raise if not transient or max retries reached
    if not should_retry or attempt == max_retries - 1:
        raise e

    delay = base_delay * (2**attempt)
    status_code = getattr(e, "status_code", "unknown")
    logger.warning(
        f"OpenAI API transient error (status {status_code}), "
        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
    )
    time.sleep(delay)


def retry_on_openai_error(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry functions on OpenAI API transient errors with exponential backoff.

    Handles 5xx server errors, rate limits, and other transient API issues.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    _handle_openai_transient_error(e, attempt, max_retries, base_delay)

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
                    _handle_openai_transient_error(e, attempt, max_retries, base_delay)

        return wrapper

    return decorator


@retry_on_openai_error()
def chat(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema=None,
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
    api_model = model if is_proxy else base_model

    from openai.types.chat import ChatCompletionMessageToolCall  # fmt: skip

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)
    response_format = _make_response_format(output_schema)

    # Build optional kwargs to avoid NOT_GIVEN/Omit type mismatch
    optional_kwargs: dict[str, Any] = {}
    if not is_reasoner:
        optional_kwargs["temperature"] = TEMPERATURE
        optional_kwargs["top_p"] = TOP_P
    if tools_dict:
        optional_kwargs["tools"] = tools_dict
    if response_format is not None:
        optional_kwargs["response_format"] = response_format

    response = client.chat.completions.create(
        model=api_model.split("@")[0],
        messages=messages_dicts,  # type: ignore[arg-type]
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider, model_meta),
        **optional_kwargs,
    )
    metadata = _record_usage(response.usage, model)
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
            logger.info("Reasoning content: %s", reasoning_content)
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


def extra_body(provider: Provider, model_meta: ModelMeta) -> dict[str, Any]:
    """Return extra body for the OpenAI API based on the model."""
    body: dict[str, Any] = {}
    if provider == "openrouter":
        # Enable detailed usage info including cached tokens
        # See: https://openrouter.ai/docs/guides/usage-accounting
        body["usage"] = {"include": True}
        if model_meta.supports_reasoning:
            body["reasoning"] = {"enabled": True, "max_tokens": 20000}
        if "@" in model_meta.model:
            provider_override = model_meta.model.split("@")[1]
            body["provider"] = {
                "order": [provider_override],
                "allow_fallbacks": False,
            }
    return body


@retry_generator_on_openai_error()
def stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema=None,
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
    api_model = model if is_proxy else base_model

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)
    response_format = _make_response_format(output_schema)
    in_reasoning_block = False
    stop_reason = None

    # Build optional kwargs to avoid NOT_GIVEN/Omit type mismatch
    optional_kwargs: dict[str, Any] = {}
    if not is_reasoner:
        optional_kwargs["temperature"] = TEMPERATURE
        optional_kwargs["top_p"] = TOP_P
    if tools_dict:
        optional_kwargs["tools"] = tools_dict
    if response_format is not None:
        optional_kwargs["response_format"] = response_format

    for chunk_raw in client.chat.completions.create(
        model=api_model.split("@")[0],
        messages=messages_dicts,  # type: ignore[call-overload]
        stream=True,
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider, model_meta),
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
    """Process remaining file attachments (images only).

    Text files are already embedded by embed_attached_file_content() in
    prepare_messages(). Only non-text files (images, binaries) remain here.

    Args:
        msg: A message dict (expected to conform to MessageDict structure)
        model: Model metadata for checking vision support

    Returns:
        The processed message dict with files converted to content parts.
        The returned dict conforms to MessageDict structure.
    """
    # Tool response messages (system + call_id) cannot contain images:
    # - OpenAI tool messages only support text content
    # - Changing role to "user" would break _handle_tools which needs "system" role
    # Drop any image files from tool responses; text content is preserved.
    if "call_id" in msg:
        msg.pop("files", None)
        # Still convert content to list format for consistency
        text = msg.get("content")
        if not isinstance(text, list):
            msg["content"] = [{"type": "text", "text": text}]
        return msg

    message_content = msg.get("content")

    # combines a content message with a list of files
    content: list[dict[str, Any]] = (
        message_content
        if isinstance(message_content, list)
        else [{"type": "text", "text": message_content}]
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
                openrouter_result.append(
                    cast(
                        MessageDict,
                        {
                            **msg,
                            "reasoning_content": reasoning,
                            "content": cleaned_content,
                        },
                    )
                )
            else:
                openrouter_result.append(cast(MessageDict, msg))
        return openrouter_result

    # Return as-is with cast for type safety
    return cast(Iterable[MessageDict], messages_dicts)


def _spec2tool(spec: ToolSpec, model: ModelMeta) -> "ChatCompletionToolParam":
    name = spec.name
    if spec.block_types:
        name = spec.block_types[0]

    description = spec.get_instructions("tool")
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
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters2dict(spec.parameters),
                # "strict": False,  # not supported by OpenRouter
            },
        }
    raise ValueError("Provider doesn't support tools API")


@lru_cache(maxsize=1)
def get_available_models(provider: Provider) -> list[ModelMeta]:
    """Get available models from a provider."""
    config = get_config()

    # Check for custom provider first (e.g., "ollama" with custom base_url)
    if is_custom_provider(provider):
        custom = next((p for p in config.user.providers if p.name == provider), None)
        if custom:
            return _get_local_models(config, provider, custom.base_url)
        # Fall through to local if custom provider not found in config
        return _get_local_models(config, provider)

    if provider == "local":
        return _get_local_models(config, provider)

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


def _get_local_models(
    config, provider_name: str = "local", base_url: str | None = None
) -> list[ModelMeta]:
    """Get available models from local provider (ollama or other OpenAI-compatible server)."""
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
        response = requests.get(models_url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # OpenAI-compatible format: {"data": [...], "object": "list"}
        raw_models = data.get("data", [])
        return [_local_model_to_modelmeta(model, provider_name) for model in raw_models]
    except requests.RequestException as e:
        logger.debug(f"Failed to retrieve models from {provider_name} provider: {e}")
        # Return empty list instead of raising - local server might not be running
        return []
    except Exception as e:
        logger.debug(f"Unexpected error retrieving {provider_name} models: {e}")
        return []


def _local_model_to_modelmeta(model_data: dict, provider_name: str) -> ModelMeta:
    """Convert local/ollama model data to ModelMeta object."""

    model_id = model_data.get("id", model_data.get("name", "unknown"))

    # Ollama models typically have context of 128k, but this may vary
    # Try to extract from model data if available
    context = model_data.get("context_length", 128_000)

    # Use CustomProvider for non-builtin providers, "local" for local provider
    provider: Provider = (
        "local" if provider_name == "local" else CustomProvider(provider_name)
    )

    return ModelMeta(
        provider=provider,
        model=model_id,
        context=context,
        max_output=None,  # Ollama doesn't typically report this
        supports_streaming=True,
        supports_vision=False,  # Could be enhanced to detect vision models
        supports_reasoning=False,
        price_input=0,  # Local models are free
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

    return ModelMeta(
        provider="openrouter",
        model=model_data.get("id", ""),
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
) -> tuple[list[MessageDict], list["ChatCompletionToolParam"] | None]:
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

    # Process message dicts - internal functions use dict[str, Any] for flexibility
    messages_dicts: Iterable[dict[str, Any]] = (
        _process_file(msg, model_meta) for msg in msgs2dicts(messages)
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
