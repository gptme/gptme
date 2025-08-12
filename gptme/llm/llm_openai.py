import json
import logging
from collections.abc import Generator, Iterable
from functools import lru_cache
from typing import TYPE_CHECKING, Any, cast

import requests

from ..config import Config, get_config
from ..constants import TEMPERATURE, TOP_P
from ..message import Message, msgs2dicts
from ..tools import ToolSpec
from .models import ModelMeta, Provider
from .utils import (
    extract_tool_uses_from_assistant_message,
    parameters2dict,
    process_image_file,
)

if TYPE_CHECKING:
    # noreorder
    from openai import OpenAI  # fmt: skip
    from openai.types.chat import ChatCompletionToolParam  # fmt: skip

# Dictionary to store clients for each provider
clients: dict[Provider, "OpenAI"] = {}
logger = logging.getLogger(__name__)

# TODO: improve provider routing for openrouter: https://openrouter.ai/docs/provider-routing
# TODO: set required-parameters: https://openrouter.ai/docs/provider-routing#required-parameters-_beta_
# TODO: set quantization: https://openrouter.ai/docs/provider-routing#quantization


ALLOWED_FILE_EXTS = ["jpg", "jpeg", "png", "gif"]


def init(provider: Provider, config: Config):
    """Initialize OpenAI client for a given provider."""
    from openai import AzureOpenAI, OpenAI  # fmt: skip

    if provider == "openai":
        proxy_key = config.get_env("LLM_PROXY_API_KEY")
        proxy_url = config.get_env("LLM_PROXY_URL")
        api_key = proxy_key or config.get_env_required("OPENAI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key,
            base_url=proxy_url or None,
        )
    elif provider == "azure":
        api_key = config.get_env_required("AZURE_OPENAI_API_KEY")
        azure_endpoint = config.get_env_required("AZURE_OPENAI_ENDPOINT")
        clients[provider] = AzureOpenAI(
            api_key=api_key,
            api_version="2023-07-01-preview",
            azure_endpoint=azure_endpoint,
        )
    elif provider == "openrouter":
        proxy_key = config.get_env("LLM_PROXY_API_KEY")
        proxy_url = config.get_env("LLM_PROXY_URL")
        api_key = proxy_key or config.get_env_required("OPENROUTER_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url=proxy_url or "https://openrouter.ai/api/v1"
        )
    elif provider == "gemini":
        api_key = config.get_env_required("GEMINI_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://generativelanguage.googleapis.com/v1beta"
        )
    elif provider == "xai":
        api_key = config.get_env_required("XAI_API_KEY")
        clients[provider] = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    elif provider == "groq":
        api_key = config.get_env_required("GROQ_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.groq.com/openai/v1"
        )
    elif provider == "deepseek":
        api_key = config.get_env_required("DEEPSEEK_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://api.deepseek.com/v1"
        )
    elif provider == "nvidia":
        api_key = config.get_env_required("NVIDIA_API_KEY")
        clients[provider] = OpenAI(
            api_key=api_key, base_url="https://integrate.api.nvidia.com/v1"
        )
    elif provider == "local":
        # OPENAI_API_BASE renamed to OPENAI_BASE_URL: https://github.com/openai/openai-python/issues/745
        api_base = config.get_env("OPENAI_API_BASE")
        api_base = api_base or config.get_env("OPENAI_BASE_URL")
        if not api_base:
            raise KeyError("Missing environment variable OPENAI_BASE_URL")
        api_key = config.get_env("OPENAI_API_KEY") or "ollama"
        clients[provider] = OpenAI(api_key=api_key, base_url=api_base)
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
        if msg.role == "system":
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
                content=f"{last_message.content}\n{msg.content}"
            )
            continue
        else:
            yield last_message
            last_message = msg

    if last_message:
        yield last_message


assert (
    len(
        list(
            _merge_consecutive(
                [Message(role="user", content="a"), Message(role="user", content="b")]
            )
        )
    )
    == 1
)


def _prep_deepseek_reasoner(msgs: list[Message]) -> Generator[Message, None, None]:
    yield msgs[0]
    yield from _merge_consecutive(_prep_o1(msgs[1:]))


def _is_reasoner(base_model: str) -> bool:
    is_o1 = any(base_model.startswith(om) for om in ["o1", "o3", "o4"])
    is_deepseek_reasoner = base_model == "deepseek-reasoner"
    is_gpt5 = base_model.startswith("gpt-5")
    return is_o1 or is_deepseek_reasoner or is_gpt5


@lru_cache(maxsize=2)
def _is_proxy(client: "OpenAI") -> bool:
    proxy_url = get_config().get_env("LLM_PROXY_URL")
    # If client has the proxy URL set, it is using the proxy
    return bool(proxy_url) and client.base_url == proxy_url


def chat(messages: list[Message], model: str, tools: list[ToolSpec] | None) -> str:
    # This will generate code and such, so we need appropriate temperature and top_p params
    # top_p controls diversity, temperature controls randomness

    from . import _get_base_model, get_provider_from_model  # fmt: skip

    provider = get_provider_from_model(model)
    client = get_client(provider)
    is_proxy = _is_proxy(client)

    base_model = _get_base_model(model)
    is_reasoner = _is_reasoner(base_model)

    # make the model name prefix with the provider if using LLM_PROXY, to make proxy aware of the provider
    api_model = model if is_proxy else base_model

    from openai import NOT_GIVEN  # fmt: skip
    from openai.types.chat import ChatCompletionMessageToolCall  # fmt: skip

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)

    response = client.chat.completions.create(
        model=api_model,
        messages=messages_dicts,  # type: ignore
        temperature=TEMPERATURE if not is_reasoner else NOT_GIVEN,
        top_p=TOP_P if not is_reasoner else NOT_GIVEN,
        tools=tools_dict if tools_dict else NOT_GIVEN,
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider),
    )
    choice = response.choices[0]
    result = []
    if choice.finish_reason == "tool_calls":
        for tool_call in choice.message.tool_calls or []:
            assert isinstance(tool_call, ChatCompletionMessageToolCall)
            result.append(
                f"@{tool_call.function.name}({tool_call.id}): {tool_call.function.arguments}"
            )
    else:
        if reasoning_content := (
            getattr(choice.message, "reasoning_content", None)
            or getattr(choice.message, "reasoning", None)
        ):
            logger.info("Reasoning content: %s", reasoning_content)
        if choice.message.content:
            result.append(choice.message.content)

    assert result
    return "\n".join(result)


def extra_headers(provider: Provider) -> dict[str, str]:
    """Return extra headers for the OpenAI API based on the model."""
    if provider == "openrouter":
        # Shows in rankings on openrouter.ai
        return {
            "HTTP-Referer": "https://github.com/gptme/gptme",
            "X-Title": "gptme",
        }
    return {}


def extra_body(provider: Provider) -> dict[str, Any]:
    """Return extra body for the OpenAI API based on the model."""
    if provider == "openrouter":
        return {
            # "provider": {
            #     "order": ["groq"],
            #     "sort": "throughput",
            #     "allow_fallbacks": False,
            # }
        }
    return {}


def stream(
    messages: list[Message], model: str, tools: list[ToolSpec] | None
) -> Generator[str, None, None]:
    from . import _get_base_model, get_provider_from_model  # fmt: skip

    provider = get_provider_from_model(model)
    client = get_client(provider)
    is_proxy = _is_proxy(client)

    base_model = _get_base_model(model)
    is_reasoner = _is_reasoner(base_model)

    # make the model name prefix with the provider if using LLM_PROXY, to make proxy aware of the provider
    api_model = model if is_proxy else base_model

    from openai import NOT_GIVEN  # fmt: skip

    messages_dicts, tools_dict = _prepare_messages_for_api(messages, model, tools)
    in_reasoning_block = False
    stop_reason = None

    for chunk_raw in client.chat.completions.create(
        model=api_model,
        messages=messages_dicts,  # type: ignore
        temperature=TEMPERATURE if not is_reasoner else NOT_GIVEN,
        top_p=TOP_P if not is_reasoner else NOT_GIVEN,
        stream=True,
        tools=tools_dict if tools_dict else NOT_GIVEN,
        extra_headers=extra_headers(provider),
        extra_body=extra_body(provider),
    ):
        from openai.types.chat import ChatCompletionChunk  # fmt: skip
        from openai.types.chat.chat_completion_chunk import (  # fmt: skip
            ChoiceDeltaToolCall,
            ChoiceDeltaToolCallFunction,
        )

        # Cast the chunk to the correct type
        chunk = cast(ChatCompletionChunk, chunk_raw)

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


def _handle_tools(message_dicts: Iterable[dict]) -> Generator[dict, None, None]:
    for message in message_dicts:
        # Format tool result as expected by the model
        if message["role"] == "system" and "call_id" in message:
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

            # Format tool uses for OpenAI API
            tool_calls = []
            for tooluse in tool_uses:
                tool_calls.append(
                    {
                        "id": tooluse.call_id or "",
                        "type": "function",
                        "function": {
                            "name": tooluse.tool,
                            "arguments": json.dumps(tooluse.kwargs or {}),
                        },
                    }
                )

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
    messages_dicts: Iterable[dict],
) -> list[dict]:  # Generator[dict, None, None]:
    """
    When we call a tool, this tool can potentially yield multiple messages. However
    the API expect to have only one tool result per tool call. This function tries
    to merge subsequent tool results with the same call ID as expected by
    the API.
    """

    messages_dicts = iter(messages_dicts)

    messages_new: list[dict] = []
    while message := next(messages_dicts, None):
        if messages_new and (
            message["role"] == "tool"
            and messages_new[-1]["role"] == "tool"
            and message["tool_call_id"] == messages_new[-1]["tool_call_id"]
        ):
            prev_msg = messages_new[-1]
            content = message["content"]
            if not isinstance(content, list):
                content = {"type": "text", "text": content}

            messages_new[-1] = {
                "role": "tool",
                "content": prev_msg["content"] + content,
                "tool_call_id": prev_msg["tool_call_id"],
            }
        else:
            messages_new.append(message)

    return messages_new


def _process_file(msg: dict, model: ModelMeta) -> dict:
    message_content = msg["content"]
    if model.provider == "deepseek":
        # deepseek does not support files
        return msg

    # combines a content message with a list of files
    content: list[dict[str, Any]] = (
        message_content
        if isinstance(message_content, list)
        else [{"type": "text", "text": message_content}]
    )

    has_images = False

    for f in msg.get("files", []):

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

    if msg["role"] == "system" and has_images:
        # Images must come from user with openai
        msg["role"] = "user"

    return msg


def _transform_msgs_for_special_provider(
    messages_dicts: Iterable[dict], model: ModelMeta
):
    if model.provider == "groq":
        # groq needs message.content to be a string
        return [{**msg, "content": msg["content"][0]["text"]} for msg in messages_dicts]
    return messages_dicts


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

    if model.provider in ["openai", "azure", "openrouter", "deepseek", "local"]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters2dict(spec.parameters),
                # "strict": False,  # not supported by OpenRouter
            },
        }
    else:
        raise ValueError("Provider doesn't support tools API")


@lru_cache(maxsize=1)
def get_available_models(provider: Provider) -> list[ModelMeta]:
    """Get available models from a provider."""
    if provider != "openrouter":
        raise ValueError(f"Provider {provider} does not support listing models")

    config = get_config()
    api_key = config.get_env_required("OPENROUTER_API_KEY")

    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    try:
        response = requests.get(
            "https://openrouter.ai/api/v1/models", headers=headers, timeout=30
        )
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


def openrouter_model_to_modelmeta(model_data: dict) -> ModelMeta:
    """Convert OpenRouter model data to ModelMeta object."""
    pricing = model_data.get("pricing", {})

    return ModelMeta(
        provider="openrouter",
        model=model_data.get("id", ""),
        context=model_data.get("context_length", 128_000),
        max_output=model_data.get("max_completion_tokens"),
        supports_streaming=True,  # Most OpenRouter models support streaming
        supports_vision="vision"
        in model_data.get("architecture", {}).get("modality", ""),
        supports_reasoning=False,  # Would need to check model-specific capabilities
        price_input=float(pricing.get("prompt", 0))
        * 1_000_000,  # Convert to per-1M tokens
        price_output=float(pricing.get("completion", 0))
        * 1_000_000,  # Convert to per-1M tokens
    )


def _prepare_messages_for_api(
    messages: list[Message], model: str, tools: list[ToolSpec] | None
) -> tuple[Iterable[dict], Iterable["ChatCompletionToolParam"] | None]:
    from . import _get_base_model  # fmt: skip
    from .models import get_model  # fmt: skip

    model_meta = get_model(model)

    is_o1 = _get_base_model(model).startswith("o1")
    if is_o1:
        messages = list(_prep_o1(messages))
    if model_meta.model == "deepseek-reasoner":
        messages = list(_prep_deepseek_reasoner(messages))

    messages_dicts: Iterable[dict] = (
        _process_file(msg, model_meta) for msg in msgs2dicts(messages)
    )

    tools_dict = [_spec2tool(tool, model_meta) for tool in tools] if tools else None

    if tools_dict is not None:
        messages_dicts = _merge_tool_results_with_same_call_id(
            _handle_tools(messages_dicts)
        )

    messages_dicts = _transform_msgs_for_special_provider(messages_dicts, model_meta)

    return list(messages_dicts), tools_dict
