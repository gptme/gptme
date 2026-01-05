import logging
import os
import time
from collections.abc import Generator, Iterable
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypedDict,
    Union,
    cast,
)

from httpx import RemoteProtocolError
from pydantic import BaseModel  # fmt: skip

from ..constants import TEMPERATURE, TOP_P
from ..message import Message, MessageMetadata, msgs2dicts
from ..telemetry import record_llm_request
from ..tools.base import ToolSpec
from .models import ModelMeta, get_model
from .utils import (
    apply_cache_control,
    extract_tool_uses_from_assistant_message,
    parameters2dict,
    process_image_file,
)

ENV_REASONING = "GPTME_REASONING"
ENV_REASONING_BUDGET = "GPTME_REASONING_BUDGET"

if TYPE_CHECKING:
    # noreorder
    import anthropic.types  # fmt: skip
    from anthropic import Anthropic  # fmt: skip

logger = logging.getLogger(__name__)

_anthropic: "Anthropic | None" = None
_is_proxy: bool = False


def _inject_schema_instruction(messages, schema_name):
    """Inject instruction to use schema tool in the last user message.

    Args:
        messages: List of message dicts
        schema_name: Name of the schema to use

    Returns:
        Modified messages list
    """
    if not messages:
        return messages

    # Find last user message
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return messages

    # Inject instruction in the content
    instruction = f"\n\nIMPORTANT: Return your response using the `return_{schema_name.lower()}` tool to ensure proper formatting."

    last_msg = messages[last_user_idx]
    if isinstance(last_msg["content"], str):
        last_msg["content"] = last_msg["content"] + instruction
    elif isinstance(last_msg["content"], list):
        # If content is a list, append as text block
        last_msg["content"].append({"type": "text", "text": instruction})

    return messages


def _extract_schema_result(content_blocks):
    """Extract structured result from tool call response.

    Args:
        content_blocks: Response content blocks from Anthropic

    Returns:
        Extracted tool call result or None
    """
    for block in content_blocks:
        if block.type == "tool_use" and block.name.startswith("return_"):
            # Return the tool input as the structured result
            return block.input
    return None


def _record_usage(
    usage: Union["anthropic.types.Usage", "anthropic.types.MessageDeltaUsage"],
    model: str,
) -> MessageMetadata | None:
    """Record usage metrics as telemetry and return MessageMetadata."""
    if not usage:
        return None

    # Extract token counts
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    cache_creation_tokens = getattr(usage, "cache_creation_input_tokens", None)
    cache_read_tokens = getattr(usage, "cache_read_input_tokens", None)

    # Calculate total tokens
    total_tokens = 0
    total_tokens += input_tokens or 0
    total_tokens += output_tokens or 0
    total_tokens += cache_creation_tokens or 0
    total_tokens += cache_read_tokens or 0

    # Record the LLM request with token usage
    record_llm_request(
        provider="anthropic",
        model=model,
        success=True,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        total_tokens=total_tokens if total_tokens > 0 else None,
    )

    # Calculate cost for metadata
    from ..telemetry import _calculate_llm_cost

    cost = _calculate_llm_cost(
        provider="anthropic",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
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
    if cache_creation_tokens is not None:
        metadata["cache_creation_tokens"] = cache_creation_tokens
    if cost > 0:
        metadata["cost"] = cost
    return metadata


def _should_use_thinking(model_meta: ModelMeta, tools: list[ToolSpec] | None) -> bool:
    # Support environment variable to override reasoning behavior
    env_reasoning = os.environ.get(ENV_REASONING)
    if env_reasoning and env_reasoning.lower() in ("1", "true", "yes"):
        return True
    elif env_reasoning and env_reasoning.lower() in ("0", "false", "no"):
        return False

    # Only enable thinking for supported models and when not using `tool` format
    if not model_meta.supports_reasoning:
        return False
    if tools:
        # FIXME: support this by adhering to anthropic's signature restrictions
        logger.warning("Tool format `tool` is not supported with reasoning yet.")
        return False
    return True


def _handle_anthropic_transient_error(e, attempt, max_retries, base_delay):
    """Handle Anthropic API transient errors with exponential backoff.

    Retries on:
    - 5xx server errors (500-599): Internal errors, bad gateway, service unavailable, etc.
    - 429 rate limit errors: Should back off and retry
    - Error messages containing 'overload', 'internal', 'timeout': Known transient issues
    """
    # Allow tests to override max_retries via environment variable
    # This breaks out of the retry loop early to prevent test timeouts
    from anthropic import APIStatusError  # fmt: skip

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
    if isinstance(e, APIStatusError):
        # Retry on all 5xx server errors (transient)
        if 500 <= e.status_code < 600:
            should_retry = True
        # Retry on 429 rate limit (should back off)
        elif e.status_code == 429:
            should_retry = True
        # Also check error message for known transient issues
        elif hasattr(e, "message"):
            error_msg = str(e.message).lower()
            if any(
                keyword in error_msg for keyword in ["overload", "internal", "timeout"]
            ):
                should_retry = True
    # Also check for "httpx.RemoteProtocolError: peer closed connection without sending complete message body"
    elif isinstance(e, RemoteProtocolError):
        should_retry = True

    # Re-raise if not transient or max retries reached
    if not should_retry or attempt == max_retries - 1:
        raise e

    delay = base_delay * (2**attempt)
    status_code = getattr(e, "status_code", "unknown")
    logger.warning(
        f"Anthropic API transient error (status {status_code}), "
        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
    )
    if status_code in [200, "200"]:
        logger.warning(f"Status code was strangely 200. Error details: {str(e)}")
    time.sleep(delay)


def retry_on_overloaded(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry functions on Anthropic API transient errors with exponential backoff.

    Handles 5xx server errors, rate limits, and other transient API issues.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    _handle_anthropic_transient_error(
                        e, attempt, max_retries, base_delay
                    )

        return wrapper

    return decorator


def retry_generator_on_overloaded(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry generator functions on Anthropic API transient errors with exponential backoff.

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
                    _handle_anthropic_transient_error(
                        e, attempt, max_retries, base_delay
                    )

        return wrapper

    return decorator


def init(config):
    global _anthropic, _is_proxy
    proxy_url = config.get_env("LLM_PROXY_URL", None)
    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    api_key = proxy_key or config.get_env_required("ANTHROPIC_API_KEY")

    from anthropic import NOT_GIVEN, Anthropic  # fmt: skip

    # Get configurable API timeout (default: client's own default of 10 minutes)
    # If not set explicitly via LLM_API_TIMEOUT, we use NOT_GIVEN to let the
    # client use its own default behavior, which handles streaming vs non-streaming
    # requests differently and may evolve with future client versions.
    timeout_str = config.get_env("LLM_API_TIMEOUT")
    timeout = float(timeout_str) if timeout_str else NOT_GIVEN

    _anthropic = Anthropic(
        api_key=api_key,
        max_retries=5,
        base_url=proxy_url or None,
        timeout=timeout,
    )
    _is_proxy = proxy_url is not None


def get_client() -> "Anthropic | None":
    return _anthropic


class CacheControl(TypedDict):
    type: Literal["ephemeral"]


def _make_schema_tool(
    output_schema: "type[BaseModel] | None",
) -> "anthropic.types.ToolParam | None":
    """Convert Pydantic BaseModel to Anthropic tool definition for constrained output."""
    if output_schema is None:
        return None

    # Extract JSON schema from Pydantic model
    json_schema = output_schema.model_json_schema()
    schema_name = output_schema.__name__

    # Convert to Anthropic tool definition
    return cast(
        "anthropic.types.ToolParam",
        {
            "name": f"return_{schema_name.lower()}",
            "description": f"Return structured output conforming to {schema_name} schema",
            "input_schema": json_schema,
        },
    )


@retry_on_overloaded()
def chat(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type[BaseModel] | None = None,
) -> tuple[str, MessageMetadata | None]:
    from anthropic import NOT_GIVEN  # fmt: skip

    if not _anthropic:
        raise RuntimeError("LLM not initialized")
    messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
        messages, tools
    )

    # Add schema tool for constrained output
    schema_tool: anthropic.types.ToolParam | None = _make_schema_tool(output_schema)
    if schema_tool:
        # Add schema tool to tools
        if tools_dict:
            tools_dict.append(schema_tool)
        else:
            tools_dict = [schema_tool]

        # Inject instruction in system messages
        schema_instruction = f"Use the {schema_tool['name']} tool to return your response in the required format."
        if system_messages:
            system_messages.append({"type": "text", "text": schema_instruction})
        else:
            system_messages = [{"type": "text", "text": schema_instruction}]

        # Inject instruction in last user message
        assert (
            output_schema is not None
        )  # schema_tool exists means output_schema is not None
        schema_name = output_schema.__name__
        messages_dicts = _inject_schema_instruction(messages_dicts, schema_name)

    api_model = f"anthropic/{model}" if _is_proxy else model

    model_meta = get_model(f"anthropic/{model}")
    use_thinking = _should_use_thinking(model_meta, tools)
    thinking_budget = int(os.environ.get(ENV_REASONING_BUDGET, "16000"))
    max_tokens = model_meta.max_output or 4096

    response = _anthropic.messages.create(
        model=api_model,
        messages=messages_dicts,
        system=system_messages,
        temperature=TEMPERATURE if not model_meta.supports_reasoning else 1,
        top_p=TOP_P if not model_meta.supports_reasoning else NOT_GIVEN,
        max_tokens=max_tokens,
        tools=tools_dict if tools_dict else NOT_GIVEN,
        thinking=(
            {"type": "enabled", "budget_tokens": thinking_budget}
            if use_thinking
            else NOT_GIVEN
        ),
        # We set a timeout for non-streaming requests to prevent Anthropic's
        # "Streaming is strongly recommended" warning/error.
        timeout=60,
    )
    content = response.content
    metadata = _record_usage(response.usage, model)

    parsed_block = []
    for block in content:
        if block.type == "text":
            parsed_block.append(block.text)
        elif block.type == "thinking":
            parsed_block.append(f"<think>\n{block.thinking}\n</think>")
        elif block.type == "tool_use":
            parsed_block.append(f"\n@{block.name}({block.id}): {block.input}")
        else:
            logger.warning("Unknown block: %s", str(block))

    return "\n".join(parsed_block), metadata


@retry_generator_on_overloaded()
def stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type[BaseModel] | None = None,
) -> Generator[str, None, MessageMetadata | None]:
    import anthropic.types  # fmt: skip
    from anthropic import NOT_GIVEN  # fmt: skip

    # Variable to capture metadata from usage recording
    captured_metadata: MessageMetadata | None = None

    if not _anthropic:
        raise RuntimeError("LLM not initialized")
    messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
        messages, tools
    )

    # Add schema tool for constrained output
    schema_tool: anthropic.types.ToolParam | None = _make_schema_tool(output_schema)
    if schema_tool:
        # Add schema tool to tools
        if tools_dict:
            tools_dict.append(schema_tool)
        else:
            tools_dict = [schema_tool]

        # Inject instruction in system messages
        schema_instruction = f"Use the {schema_tool['name']} tool to return your response in the required format."
        if system_messages:
            system_messages.append({"type": "text", "text": schema_instruction})
        else:
            system_messages = [{"type": "text", "text": schema_instruction}]

        # Inject instruction in last user message
        assert (
            output_schema is not None
        )  # schema_tool exists means output_schema is not None
        schema_name = output_schema.__name__
        messages_dicts = _inject_schema_instruction(messages_dicts, schema_name)

    api_model = f"anthropic/{model}" if _is_proxy else model

    model_meta = get_model(f"anthropic/{model}")
    use_thinking = _should_use_thinking(model_meta, tools)
    # Use the same configurable thinking budget as chat()
    thinking_budget = int(os.environ.get(ENV_REASONING_BUDGET, "16000"))
    max_tokens = model_meta.max_output or 4096

    with _anthropic.messages.stream(
        model=api_model,
        messages=messages_dicts,
        system=system_messages,
        temperature=TEMPERATURE if not model_meta.supports_reasoning else 1,
        top_p=TOP_P if not model_meta.supports_reasoning else NOT_GIVEN,
        max_tokens=max_tokens,
        tools=tools_dict if tools_dict else NOT_GIVEN,
        thinking=(
            {"type": "enabled", "budget_tokens": thinking_budget}
            if use_thinking
            else NOT_GIVEN
        ),
    ) as stream:
        for chunk in stream:
            match chunk.type:
                case "content_block_start":
                    chunk = cast(anthropic.types.RawContentBlockStartEvent, chunk)
                    block = chunk.content_block
                    if isinstance(block, anthropic.types.ToolUseBlock):
                        tool_use = block
                        yield f"\n@{tool_use.name}({tool_use.id}): "
                    elif isinstance(block, anthropic.types.ThinkingBlock):
                        yield "<think>\n"
                    elif isinstance(block, anthropic.types.RedactedThinkingBlock):
                        yield "<think redacted>\n"
                    elif isinstance(block, anthropic.types.TextBlock):
                        if block.text:
                            logger.warning("unexpected text block: %s", block.text)
                    else:
                        print(f"Unknown block type: {block}")
                case "content_block_delta":
                    chunk = cast(anthropic.types.RawContentBlockDeltaEvent, chunk)
                    delta = chunk.delta
                    if isinstance(delta, anthropic.types.TextDelta):
                        yield delta.text
                    elif isinstance(delta, anthropic.types.ThinkingDelta):
                        yield delta.thinking
                    elif isinstance(delta, anthropic.types.InputJSONDelta):
                        yield delta.partial_json
                    elif isinstance(delta, anthropic.types.SignatureDelta):
                        # delta.signature
                        pass
                    else:
                        logger.warning("Unknown delta type: %s", delta)
                case "content_block_stop":
                    stop_chunk = cast(anthropic.types.ContentBlockStopEvent, chunk)
                    stop_block = stop_chunk.content_block  # type: ignore
                    if isinstance(stop_block, anthropic.types.TextBlock):
                        pass
                    elif isinstance(stop_block, anthropic.types.ToolUseBlock):
                        pass
                    elif isinstance(stop_block, anthropic.types.ThinkingBlock):
                        yield "\n</think>\n\n"
                    elif isinstance(stop_block, anthropic.types.RedactedThinkingBlock):
                        yield "\n</think redacted>\n\n"
                    else:
                        logger.warning("Unknown stop block: %s", stop_block)
                case "text":
                    # full text message
                    pass
                case "message_start":
                    chunk = cast(
                        anthropic.types.MessageStartEvent,
                        chunk,
                    )
                    # Don't record usage here, wait for message_delta with final usage
                case "message_delta":
                    chunk = cast(anthropic.types.MessageDeltaEvent, chunk)
                    # Record usage from message_delta which contains the final/cumulative usage
                    # and capture metadata for message attachment
                    captured_metadata = _record_usage(chunk.usage, model)
                case "message_stop":
                    pass
                case _:
                    # print(f"Unknown chunk type: {chunk.type}")
                    pass

    # Return the captured metadata (accessible via StopIteration.value)
    return captured_metadata


def _handle_tools(message_dicts: Iterable[dict]) -> Generator[dict, None, None]:
    for message in message_dicts:
        # Format tool result as expected by the model
        if message["role"] == "user" and "call_id" in message:
            modified_message = dict(message)
            modified_message["content"] = [
                {
                    "type": "tool_result",
                    "content": modified_message["content"],
                    "tool_use_id": modified_message.pop("call_id"),
                }
            ]
            yield modified_message
        # Find tool_use occurrences and format them as expected
        elif message["role"] == "assistant":
            modified_message = dict(message)

            content_parts, tool_uses = extract_tool_uses_from_assistant_message(
                message["content"], tool_format_override="tool"
            )

            # Add tool uses in Anthropic format
            for tooluse in tool_uses:
                content_parts.append(
                    {
                        "type": "tool_use",
                        "id": tooluse.call_id or "",
                        "name": tooluse.tool,
                        "input": tooluse.kwargs or {},
                    }
                )

            if content_parts:
                modified_message["content"] = content_parts

            yield modified_message
        else:
            yield message


# File extensions allowed for image uploads
ALLOWED_FILE_EXTS = ["jpg", "jpeg", "png", "gif"]


def _process_file(message_dict: dict) -> dict:
    message_content = message_dict["content"]

    # combines a content message with a list of files
    content: list[dict[str, Any]] = (
        message_content
        if isinstance(message_content, list)
        else [{"type": "text", "text": message_content}]
    )

    for f in message_dict.pop("files", []):
        result = process_image_file(f, content, max_size_mb=5, expand_user=True)
        if result is None:
            continue

        data, media_type = result
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }
        )

    message_dict["content"] = content
    return message_dict


def _transform_system_messages(
    messages: list[Message],
) -> tuple[list[Message], list["anthropic.types.TextBlockParam"]]:
    """Transform system messages into Anthropic's expected format.

    This function:
    1. Extracts the first system message as the main system prompt
    2. Transforms subsequent system messages into <system> tags in user messages
    3. Merges consecutive user messages
    4. Applies cache control to optimize performance

    Note: Anthropic allows up to 4 cache breakpoints in a conversation.
    We use this to cache:
    1. The system prompt (if long enough)
    2. Earlier messages in multi-turn conversations

    Returns:
        tuple[list[Message], list[TextBlockParam]]: Transformed messages and system messages
    """
    if not messages or messages[0].role != "system":
        raise ValueError(
            f"First message must be a system message, got {messages[0].role if messages else 'empty list'}"
        )
    system_prompt = messages[0].content
    messages = messages.copy()
    messages.pop(0)

    # Convert subsequent system messages into <system> messages,
    # unless a `call_id` is present, indicating the tool_format is 'tool'.
    # Tool responses are handled separately by _handle_tool.
    for i, message in enumerate(messages):
        if message.role == "system":
            content = (
                f"<system>{message.content}</system>"
                if message.call_id is None
                else message.content
            )

            messages[i] = Message(
                "user",
                content=content,
                files=message.files,  # type: ignore
                call_id=message.call_id,
            )

    # find consecutive user role messages and merge them together
    messages_new: list[Message] = []
    while messages:
        message = messages.pop(0)
        if (
            messages_new
            and messages_new[-1].role == "user"
            and message.role == "user"
            and message.call_id == messages_new[-1].call_id
        ):
            messages_new[-1] = Message(
                "user",
                content=f"{messages_new[-1].content}\n\n{message.content}",
                files=messages_new[-1].files + message.files,  # type: ignore
                call_id=messages_new[-1].call_id,
            )
        else:
            messages_new.append(message)
    messages = messages_new
    system_messages: list[anthropic.types.TextBlockParam] = [
        {
            "type": "text",
            "text": system_prompt,
        }
    ]

    return messages, system_messages


def _spec2tool(
    spec: ToolSpec,
) -> "anthropic.types.ToolParam":
    name = spec.name
    if spec.block_types:
        name = spec.block_types[0]

    # TODO: are input_schema and parameters the same? (both JSON Schema?)
    return cast(
        "anthropic.types.ToolParam",
        {
            "name": name,
            "description": spec.get_instructions("tool"),
            "input_schema": parameters2dict(spec.parameters),
        },
    )


def _prepare_messages_for_api(
    messages: list[Message], tools: list[ToolSpec] | None
) -> tuple[
    list["anthropic.types.MessageParam"],
    list["anthropic.types.TextBlockParam"],
    list["anthropic.types.ToolParam"] | None,
]:
    """Prepare messages for the Anthropic API.

    This function:
    1. Transforms system messages
    2. Handles file attachments
    3. Applies cache control
    4. Prepares tools

    Args:
        messages: List of messages to prepare
        tools: List of tool specifications

    Returns:
        tuple containing:
        - Prepared message dictionaries
        - System messages
        - Tool dictionaries (if tools provided)
    """
    # noreorder
    import anthropic.types  # fmt: skip

    # Transform system messages
    messages, system_messages = _transform_system_messages(messages)

    # Handle files and convert to dicts
    messages_dicts = (_process_file(f) for f in msgs2dicts(messages))

    # Prepare tools
    tools_dict = [_spec2tool(tool) for tool in tools] if tools else None

    if tools_dict is not None:
        messages_dicts = _handle_tools(messages_dicts)

    # Apply cache control to optimize performance
    messages_dicts_new: list[anthropic.types.MessageParam] = []
    for msg in messages_dicts:
        content_parts: list[
            anthropic.types.TextBlockParam
            | anthropic.types.ImageBlockParam
            | anthropic.types.ToolUseBlockParam
            | anthropic.types.ToolResultBlockParam
        ] = []
        raw_content = (
            msg["content"]
            if isinstance(msg["content"], list)
            else [{"type": "text", "text": msg["content"]}]
        )

        for part in raw_content:
            if isinstance(part, dict):
                content_parts.append(part)  # type: ignore
            else:
                content_parts.append({"type": "text", "text": str(part)})

        # Anthropic API rejects messages with trailing whitespace in the last assistant message.
        # We remove trailing whitespace from all assistant messages to ensure consistent requests for caching.
        if msg["role"] == "assistant":
            for item in content_parts:
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    item = cast(anthropic.types.TextBlockParam, item)
                    item["text"] = item["text"].rstrip()

        # Filter out empty text blocks to prevent API errors
        filtered_parts = []
        for part in content_parts:
            if part.get("type") == "text":
                text_content = part.get("text", "")
                # Skip empty text blocks
                if isinstance(text_content, str) and text_content.strip():
                    filtered_parts.append(part)
            else:
                # Keep all non-text parts
                filtered_parts.append(part)
        content_parts = filtered_parts

        # Only add message if it has content (prevents Anthropic API error)
        if content_parts:
            messages_dicts_new.append({"role": msg["role"], "content": content_parts})
        else:
            logger.warning(
                f"Skipping message with role '{msg['role']}' - all content was filtered out"
            )

    # Apply cache control for Anthropic prompt caching
    # See: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
    messages_with_cache, system_with_cache = apply_cache_control(
        cast(list[dict], messages_dicts_new),
        cast(list[dict] | None, system_messages),
    )
    messages_dicts_new = cast(list[anthropic.types.MessageParam], messages_with_cache)
    system_messages = cast(list[anthropic.types.TextBlockParam], system_with_cache)

    return messages_dicts_new, system_messages, tools_dict
