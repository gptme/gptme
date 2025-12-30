import logging
import shutil
import sys
import time
from collections.abc import Generator, Iterator
from functools import lru_cache
from pathlib import Path
from typing import cast

from rich import print as rprint

from ..config import Config, get_config
from ..constants import prompt_assistant
from ..message import Message, MessageMetadata, format_msgs, len_tokens
from ..telemetry import trace_function
from ..tools import ToolSpec, ToolUse
from ..util import console
from .llm_anthropic import chat as chat_anthropic
from .llm_anthropic import get_client as get_anthropic_client
from .llm_anthropic import init as init_anthropic
from .llm_anthropic import stream as stream_anthropic
from .llm_openai import chat as chat_openai
from .llm_openai import get_client as get_openai_client
from .llm_openai import init as init_openai
from .llm_openai import stream as stream_openai
from .models import (
    MODELS,
    PROVIDERS_OPENAI,
    CustomProvider,
    ModelMeta,
    Provider,
    get_default_model_summary,
    is_custom_provider,
)

logger = logging.getLogger(__name__)


def init_llm(provider: Provider):
    """Initialize LLM client for a given provider if not already initialized.

    Args:
        provider: Provider name (built-in or custom)
    """
    config = get_config()

    # Check if it's a built-in OpenAI-compatible provider
    if provider in PROVIDERS_OPENAI and not get_openai_client(provider):
        init_openai(provider, config)
    # Check if it's a custom provider (OpenAI-compatible)
    elif is_custom_provider(provider) and not get_openai_client(provider):
        init_openai(provider, config)
    elif provider == "anthropic" and not get_anthropic_client():
        init_anthropic(config)
    else:
        logger.debug(f"Provider {provider} already initialized or unknown")


def _get_agent_name(config: Config) -> str | None:
    agent_config = config.chat and config.chat.agent_config
    return agent_config.name if agent_config and agent_config.name else None


@trace_function(name="llm.reply", attributes={"component": "llm"})
def reply(
    messages: list[Message],
    model: str,
    stream: bool = False,
    tools: list[ToolSpec] | None = None,
    workspace: Path | None = None,
    output_schema: type | None = None,
) -> Message:
    # Trigger GENERATION_PRE hooks and collect context messages
    from ..hooks import HookType, trigger_hook

    context_msgs = list(
        trigger_hook(
            HookType.GENERATION_PRE, messages, workspace=workspace, manager=None
        )
    )

    # Add context messages for generation (don't modify original messages)
    generation_msgs = list(messages)  # Create a copy
    if context_msgs:
        generation_msgs.extend(context_msgs)

    init_llm(get_provider_from_model(model))
    config = get_config()
    agent_name = _get_agent_name(config)
    if stream:
        break_on_tooluse = bool(config.get_env_bool("GPTME_BREAK_ON_TOOLUSE", True))
        return _reply_stream(
            generation_msgs,
            model,
            tools,
            break_on_tooluse,
            agent_name=agent_name,
            output_schema=output_schema,
        )
    else:
        rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")
        response, metadata = _chat_complete(
            generation_msgs, model, tools, output_schema=output_schema
        )
        rprint(" " * shutil.get_terminal_size().columns, end="\r")
        rprint(f"{prompt_assistant(agent_name)}: {response}")
        return Message("assistant", response, metadata=metadata)


def get_provider_from_model(model: str) -> Provider:
    """Extract provider from fully qualified model name.

    Returns the provider (built-in BuiltinProvider or CustomProvider).
    """
    if "/" not in model:
        raise ValueError(
            f"Model name must be fully qualified with provider prefix: {model}"
        )
    provider_str = model.split("/")[0]

    # Check built-in providers first
    if provider_str in MODELS:
        return cast(Provider, provider_str)

    # Check custom providers from config - wrap in CustomProvider
    if is_custom_provider(provider_str):
        return CustomProvider(provider_str)

    raise ValueError(f"Unknown provider: {provider_str}")


def _get_base_model(model: str) -> str:
    """Get base model name without provider prefix."""
    return model.split("/", 1)[1]


@trace_function(name="llm.chat_complete", attributes={"component": "llm"})
def _chat_complete(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type | None = None,
    max_retries: int = 3,
) -> tuple[str, MessageMetadata | None]:
    from pydantic import BaseModel, ValidationError

    provider = get_provider_from_model(model)

    # Providers with native constrained decoding support
    # Custom providers are OpenAI-compatible, so route them through the OpenAI path
    if provider in PROVIDERS_OPENAI or is_custom_provider(provider):
        return chat_openai(messages, model, tools, output_schema=output_schema)
    elif provider == "anthropic":
        return chat_anthropic(
            messages, _get_base_model(model), tools, output_schema=output_schema
        )

    # Validation-only fallback for unsupported providers
    metadata: MessageMetadata | None = None
    if output_schema is not None:
        logger = logging.getLogger(__name__)
        for attempt in range(max_retries):
            # Generate without constraints
            if provider in PROVIDERS_OPENAI:
                response, metadata = chat_openai(messages, model, tools)
            elif provider == "anthropic":
                response, metadata = chat_anthropic(
                    messages, _get_base_model(model), tools
                )
            else:
                raise ValueError(f"Unsupported provider: {provider}")

            # Validate response
            try:
                if isinstance(output_schema, type) and issubclass(
                    output_schema, BaseModel
                ):
                    output_schema.model_validate_json(response)
                return response, metadata  # Validation succeeded
            except ValidationError as e:
                if attempt < max_retries - 1:
                    # Add validation error to context for retry
                    messages = messages + [
                        Message(
                            "user",
                            f"Validation error: {e}. Please ensure your response follows the required schema and try again.",
                        )
                    ]
                    logger.warning(
                        f"Validation attempt {attempt + 1}/{max_retries} failed: {e}"
                    )
                else:
                    # Out of retries, return response anyway with warning
                    logger.warning(
                        f"Failed to validate response after {max_retries} attempts: {e}"
                    )
                    return response, metadata

    # No schema requested, generate normally
    if provider in PROVIDERS_OPENAI:
        return chat_openai(messages, model, tools)
    elif provider == "anthropic":
        return chat_anthropic(messages, _get_base_model(model), tools)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


class _StreamWithMetadata:
    """Wrapper that captures a generator's return value (metadata)."""

    def __init__(self, gen: Generator[str, None, MessageMetadata | None], model: str):
        self.gen = gen
        self.model = model
        self.metadata: MessageMetadata | None = None

    def __iter__(self) -> Iterator[str]:
        try:
            while True:
                yield next(self.gen)
        except StopIteration as e:
            self.metadata = e.value
            # Ensure model is set in metadata even if provider didn't include it
            if self.metadata is None:
                self.metadata = {"model": self.model}
            elif "model" not in self.metadata:
                self.metadata["model"] = self.model


@trace_function(name="llm.stream", attributes={"component": "llm"})
def _stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type | None = None,
) -> _StreamWithMetadata:
    provider = get_provider_from_model(model)
    # Custom providers are OpenAI-compatible, so route them through the OpenAI path
    if provider in PROVIDERS_OPENAI or is_custom_provider(provider):
        gen = stream_openai(messages, model, tools, output_schema=output_schema)
        return _StreamWithMetadata(gen, model)
    elif provider == "anthropic":
        gen = stream_anthropic(
            messages, _get_base_model(model), tools, output_schema=output_schema
        )
        return _StreamWithMetadata(gen, model)
    else:
        # Note: Validation-only fallback for streaming is complex
        # For now, unsupported providers don't support output_schema in streaming mode
        if output_schema is not None:
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Provider {provider} does not support output_schema in streaming mode"
            )
        raise ValueError(f"Unsupported provider: {provider}")


@trace_function(name="llm.reply_stream", attributes={"component": "llm"})
def _reply_stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    break_on_tooluse: bool = True,
    agent_name: str | None = None,
    output_schema: type | None = None,
) -> Message:
    rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")

    def print_clear(length: int = 0):
        length = length or shutil.get_terminal_size().columns
        rprint("\r" + " " * length, end="\r")

    output = ""
    start_time = time.time()
    first_token_time = None
    are_thinking = False

    # Create stream wrapper to capture metadata
    stream = _stream(messages, model, tools, output_schema=output_schema)

    try:
        for char in (char for chunk in stream for char in chunk):
            if not output:  # first character
                first_token_time = time.time()
                print_clear()
                rprint(f"{prompt_assistant(agent_name)}: \n", end="")

            # Check for thinking tags before printing a newline
            if char == "\n" or not output:
                last_line = output.rsplit("\n", 1)[-1]
                if last_line.strip():
                    # print(f"(check line: {last_line})", end="")
                    pass

                # Check for opening tag at the end of this line
                if last_line == "<think>" or last_line == "<thinking>":
                    # Print spaces to clear the line
                    print_clear(len(last_line))
                    # Print styled version
                    rprint(f"[dim]{last_line}[/dim]", end="")
                    are_thinking = True
                # Check for closing tag
                elif last_line == "</think>" or last_line == "</thinking>":
                    print_clear(len(last_line))
                    # Print styled version
                    rprint(f"[dim]{last_line}[/dim]", end="")
                    are_thinking = False

                # Now print the newline
                rprint(char, end="")
            else:
                # Print normal characters
                if are_thinking:
                    rprint(f"[dim]{char}[/dim]", end="")
                else:
                    rprint(char, end="")

            assert len(char) == 1
            output += char

            # need to flush stdout to get the print to show up
            sys.stdout.flush()

            # Trigger the tool detection only if the line is finished.
            # Helps to detect nested start code blocks.
            if break_on_tooluse and char == "\n":
                # TODO: make this more robust/general, maybe with a callback that runs on each char/chunk
                # pause inference on finished code-block, letting user run the command before continuing
                # Use streaming=True to require blank line after code blocks during streaming
                tooluses = [
                    tooluse
                    for tooluse in ToolUse.iter_from_content(output, streaming=True)
                    if tooluse.is_runnable
                ]
                if tooluses:
                    logger.debug("Found tool use, breaking")
                    break

    except KeyboardInterrupt:
        return Message(
            "assistant", output + "... ^C Interrupted", metadata=stream.metadata
        )
    finally:
        # Explicitly close the underlying generator to release resources
        # This handles all exit paths: normal completion, KeyboardInterrupt, and tool break
        stream.gen.close()
        print_clear()
        if first_token_time:
            end_time = time.time()
            logger.debug(
                f"Generation finished in {end_time - start_time:.1f}s "
                f"(ttft: {first_token_time - start_time:.2f}s, "
                f"gen: {end_time - first_token_time:.2f}s, "
                f"tok/s: {len_tokens(output, model)/(end_time - first_token_time):.1f})"
            )

    return Message("assistant", output, metadata=stream.metadata)


@trace_function(name="llm.summarize", attributes={"component": "llm"})
def _summarize_str(content: str) -> str:
    """
    Summarizes a long text using a LLM.

    To summarize messages or the conversation log,
    use `gptme.tools.summarize` instead (which wraps this).
    """
    messages = [
        Message(
            "system",
            content="You are a helpful assistant that helps summarize messages into bullet format. Dont use any preamble or heading, start directly with a bullet list.",
        ),
        Message("user", content=f"Summarize this:\n{content}"),
    ]

    model = get_default_model_summary()
    assert model, "No default model set"

    if len_tokens(messages, model.model) > model.context:
        raise ValueError(
            f"Cannot summarize more than {model.context} tokens, got {len_tokens(messages, model.model)}"
        )

    summary, _metadata = _chat_complete(messages, model.full, None)
    assert summary
    logger.debug(
        f"Summarized long output ({len_tokens(content, model.model)} -> {len_tokens(summary, model.model)} tokens): "
        + summary
    )
    return summary


def summarize(msg: str | Message | list[Message]) -> Message:
    """Uses a cheap LLM to summarize long outputs."""
    # construct plaintext from message(s)
    if isinstance(msg, str):
        content = msg
    elif isinstance(msg, Message):
        content = msg.content
    else:
        content = "\n".join(format_msgs(msg))

    summary = _summarize_helper(content)

    # construct message from summary
    content = f"Here's a summary of the conversation:\n{summary}"
    return Message(role="system", content=content)


@lru_cache(maxsize=128)
def _summarize_helper(s: str, tok_max_start=400, tok_max_end=400) -> str:
    """
    Helper function for summarizing long outputs.
    Truncates long outputs, then summarizes.
    """
    # Use gpt-4 as default model for summarization helper
    if len_tokens(s, "gpt-4") > tok_max_start + tok_max_end:
        beginning = " ".join(s.split()[:tok_max_start])
        end = " ".join(s.split()[-tok_max_end:])
        summary = _summarize_str(beginning + "\n...\n" + end)
    else:
        summary = _summarize_str(s)
    return summary


def list_available_providers() -> list[tuple[Provider, str]]:
    """
    List all available providers based on configured API keys.

    Returns:
        List of tuples (provider, api_key_env_var) for configured providers
    """
    config = get_config()
    available = []

    provider_checks = [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("xai", "XAI_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("azure", "AZURE_OPENAI_API_KEY"),
    ]

    for provider, env_var in provider_checks:
        if config.get_env(env_var):
            available.append((cast(Provider, provider), env_var))

    return available


def guess_provider_from_config() -> Provider | None:
    """
    Guess the provider to use from the configuration.
    """
    available = list_available_providers()
    if available:
        provider, _ = available[0]  # Return first available provider
        console.log(f"Found {provider} API key, using {provider} provider")
        return provider
    return None


def get_model_from_api_key(api_key: str) -> tuple[str, Provider, str] | None:
    """
    Guess the model from the API key prefix.
    """

    if api_key.startswith("sk-ant-"):
        return api_key, "anthropic", "ANTHROPIC_API_KEY"
    elif api_key.startswith("sk-or-"):
        return api_key, "openrouter", "OPENROUTER_API_KEY"
    elif api_key.startswith("sk-"):
        return api_key, "openai", "OPENAI_API_KEY"

    return None


def get_available_models(provider: Provider) -> list[ModelMeta]:
    """
    Get available models from a provider.

    Args:
        provider: The provider to get models from

    Returns:
        List of ModelMeta objects

    Raises:
        ValueError: If provider doesn't support listing models
        Exception: If API request fails
    """
    if provider in ("openrouter", "local") or is_custom_provider(provider):
        from .llm_openai import get_available_models as get_openai_models

        return get_openai_models(provider)
    else:
        raise ValueError(f"Provider {provider} does not support listing models")
