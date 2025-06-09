import base64
import logging
import os
import time
from collections.abc import Generator, Iterable
from functools import wraps
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypedDict,
    cast,
)

from ..constants import TEMPERATURE, TOP_P
from ..message import Message, msgs2dicts
from ..tools.base import Parameter, ToolSpec, ToolUse
from .models import ModelMeta, get_model

if TYPE_CHECKING:
    # noreorder
    import anthropic.types  # fmt: skip
    from anthropic import Anthropic  # fmt: skip

logger = logging.getLogger(__name__)

_anthropic: "Anthropic | None" = None


def _should_use_thinking(model_meta: ModelMeta, tools: list[ToolSpec] | None) -> bool:
    # Only enable thinking for supported models and when not using `tool` format
    if not model_meta.supports_reasoning:
        return False
    if tools:
        # FIXME: support this by adhering to anthropic's signature restrictions
        logger.warning("Tool format `tool` is not supported with reasoning yet.")
        return False
    return True


def _handle_anthropic_overloaded(e, attempt, max_retries, base_delay):
    """Handle Anthropic API overloaded errors with exponential backoff."""
    from anthropic import APIStatusError  # fmt: skip

    if (
        not isinstance(e, APIStatusError)
        or e.status_code != 503
        or attempt == max_retries - 1
    ):
        raise e
    delay = base_delay * (2**attempt)
    logger.warning(
        f"Anthropic API overloaded, retrying in {delay}s (attempt {attempt + 1}/{max_retries})"
    )
    time.sleep(delay)


def retry_on_overloaded(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry functions on Anthropic API overloaded errors with exponential backoff."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    _handle_anthropic_overloaded(e, attempt, max_retries, base_delay)

        return wrapper

    return decorator


def retry_generator_on_overloaded(max_retries: int = 5, base_delay: float = 1.0):
    """Decorator to retry generator functions on Anthropic API overloaded errors with exponential backoff."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    yield from func(*args, **kwargs)
                    break  # If generator completes successfully, exit retry loop
                except Exception as e:
                    _handle_anthropic_overloaded(e, attempt, max_retries, base_delay)

        return wrapper

    return decorator


def init(config):
    global _anthropic
    proxy_key = config.get_env("LLM_PROXY_API_KEY")
    api_key = proxy_key or config.get_env_required("ANTHROPIC_API_KEY")
    from anthropic import Anthropic  # fmt: skip

    _anthropic = Anthropic(
        api_key=api_key,
        max_retries=5,
        base_url=config.get_env("LLM_PROXY_URL", None),
    )


def get_client() -> "Anthropic | None":
    return _anthropic


class CacheControl(TypedDict):
    type: Literal["ephemeral"]


@retry_on_overloaded()
def chat(messages: list[Message], model: str, tools: list[ToolSpec] | None) -> str:
    from anthropic import NOT_GIVEN  # fmt: skip

    assert _anthropic, "LLM not initialized"
    messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
        messages, tools
    )

    model_meta = get_model(f"anthropic/{model}")
    use_thinking = _should_use_thinking(model_meta, tools)
    thinking_budget = 16000
    max_tokens = (model_meta.max_output or 4096) + (
        thinking_budget if use_thinking else 0
    )

    response = _anthropic.messages.create(
        model=model,
        messages=messages_dicts,
        system=system_messages,
        temperature=TEMPERATURE if not use_thinking else 1,
        top_p=TOP_P if not use_thinking else NOT_GIVEN,
        max_tokens=max_tokens,
        tools=tools_dict if tools_dict else NOT_GIVEN,
        thinking=(
            {"type": "enabled", "budget_tokens": thinking_budget}
            if use_thinking
            else NOT_GIVEN
        ),
    )
    content = response.content
    logger.debug(response.usage)

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

    return "\n".join(parsed_block)


@retry_generator_on_overloaded()
def stream(
    messages: list[Message], model: str, tools: list[ToolSpec] | None
) -> Generator[str, None, None]:
    import anthropic.types  # fmt: skip
    from anthropic import NOT_GIVEN  # fmt: skip
    from anthropic.types import ToolUnionParam

    assert _anthropic, "LLM not initialized"
    messages_dicts, system_messages, tools_dict = _prepare_messages_for_api(
        messages, tools
    )

    search = os.environ.get("ANTHROPIC_WEB_SEARCH", "false").lower() == "true"
    if search:
        search_tool_dict: ToolUnionParam = {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        }
        if tools_dict is None:
            tools_dict = [search_tool_dict]
        elif not any(tool["name"] == "web_search" for tool in tools_dict):
            tools_dict.append(search_tool_dict)

    model_meta = get_model(f"anthropic/{model}")
    use_thinking = _should_use_thinking(model_meta, tools)
    thinking_budget = 16000
    max_tokens = (model_meta.max_output or 4096) + (
        thinking_budget if use_thinking else 0
    )

    with _anthropic.messages.stream(
        model=model,
        messages=messages_dicts,
        system=system_messages,
        temperature=TEMPERATURE if not use_thinking else 1,
        top_p=TOP_P if not use_thinking else NOT_GIVEN,
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
                    elif isinstance(block, anthropic.types.ServerToolUseBlock):
                        # This is a tool use block that is not yet runnable.
                        # It will be filled in later with the tool result.
                        print("Searching...")
                    elif isinstance(block, anthropic.types.WebSearchToolResultBlock):
                        # This is a web search result block.
                        # It will be filled in later with the search results.
                        print("Web search tool results")
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
                    elif isinstance(delta, anthropic.types.CitationsDelta):
                        if isinstance(
                            delta.citation,
                            anthropic.types.CitationsWebSearchResultLocation,
                        ):
                            print(delta.citation.url)
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
                    elif isinstance(stop_block, anthropic.types.ServerToolUseBlock):
                        yield f"\n@{stop_block.name}({stop_block.id}): {stop_block.input}"
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
                    logger.debug(chunk.message.usage)
                case "message_delta":
                    chunk = cast(anthropic.types.MessageDeltaEvent, chunk)
                    logger.debug(chunk.usage)
                case "message_stop":
                    pass
                case _:
                    # print(f"Unknown chunk type: {chunk.type}")
                    pass


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
            text = ""
            content = []

            # Some content are text, some are list
            if isinstance(message["content"], list):
                message_parts = message["content"]
            else:
                message_parts = [{"type": "text", "text": message["content"]}]

            for message_part in message_parts:
                if message_part["type"] != "text":
                    content.append(message_part)
                    continue

                # For a message part of type `text`` we try to extract the tool_uses
                # We search line by line to stop as soon as we have a tool call
                # It makes it easier to split in multiple parts.
                for line in message_part["text"].split("\n"):
                    text += line + "\n"

                    tooluses = [
                        tooluse
                        for tooluse in ToolUse.iter_from_content(text)
                        if tooluse.is_runnable
                    ]
                    if not tooluses:
                        continue

                    # At that point we should always have exactly one tooluse
                    # Because we remove the previous ones as soon as we encounter
                    # them so we can't have more.
                    assert len(tooluses) == 1
                    tooluse = tooluses[0]
                    # We only want to add a tool call if we have a call_id which
                    # means it is a tool response
                    if tooluse.call_id:
                        before_tool = text[: tooluse.start]

                        if before_tool.strip():
                            content.append({"type": "text", "text": before_tool})

                        content.append(
                            {
                                "type": "tool_use",
                                "id": tooluse.call_id or "",
                                "name": tooluse.tool,
                                "input": tooluse.kwargs or {},
                            }
                        )
                    else:
                        content.append({"type": "text", "text": text})
                    # The text is emptied to start over with the next lines if any.
                    text = ""

            if content:
                modified_message["content"] = content

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
        f = Path(f).expanduser()
        ext = f.suffix[1:]
        if ext not in ALLOWED_FILE_EXTS:
            logger.warning("Unsupported file type: %s", ext)
            continue
        if ext == "jpg":
            ext = "jpeg"
        media_type = f"image/{ext}"

        content.append(
            {
                "type": "text",
                "text": f"![{f.name}]({f.name}):",
            }
        )

        # read file
        data_bytes = f.read_bytes()
        data = base64.b64encode(data_bytes).decode("utf-8")

        # check that the file is not too large
        # anthropic limit is 5MB, seems to measure the base64-encoded size instead of raw bytes
        # TODO: use compression to reduce file size
        # print(f"{len(data)=}")
        if len(data) > 5 * 1_024 * 1_024:
            content.append(
                {
                    "type": "text",
                    "text": "Image size exceeds 5MB. Please upload a smaller image.",
                }
            )
            continue

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
    assert messages[0].role == "system"
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


def _parameters2dict(parameters: list[Parameter]) -> dict[str, object]:
    required = []
    properties = {}

    for param in parameters:
        if param.required:
            required.append(param.name)
        properties[param.name] = {"type": param.type, "description": param.description}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _spec2tool(
    spec: ToolSpec,
) -> "anthropic.types.ToolParam":
    name = spec.name
    if spec.block_types:
        name = spec.block_types[0]

    # TODO: are input_schema and parameters the same? (both JSON Schema?)
    return {
        "name": name,
        "description": spec.get_instructions("tool"),
        "input_schema": _parameters2dict(spec.parameters),
    }


def _prepare_messages_for_api(
    messages: list[Message], tools: list[ToolSpec] | None
) -> tuple[
    list["anthropic.types.MessageParam"],
    list["anthropic.types.TextBlockParam"],
    list["anthropic.types.ToolUnionParam"] | None,
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
    tools_dict: list[anthropic.types.ToolUnionParam] | None = (
        [_spec2tool(tool) for tool in tools] if tools else None
    )

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

        messages_dicts_new.append({"role": msg["role"], "content": content_parts})

    # set for the first system message (static between sessions)
    system_messages[0]["cache_control"] = {"type": "ephemeral"}

    # set cache points at the two last user messages, as suggested in Anthropic docs:
    # > The conversation history (previous messages) is included in the messages array.
    # > The final turn is marked with cache-control, for continuing in followups.
    # > The second-to-last user message is marked for caching with the cache_control parameter, so that this checkpoint can read from the previous cache.
    # https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching#continuing-a-multi-turn-conversation
    for msgp in [msg for msg in messages_dicts_new if msg["role"] == "user"][-2:]:
        assert isinstance(msgp["content"], list)
        msgp["content"][-1]["cache_control"] = {"type": "ephemeral"}  # type: ignore

    return messages_dicts_new, system_messages, tools_dict
