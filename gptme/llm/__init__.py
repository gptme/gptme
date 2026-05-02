import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Generator, Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from rich import print as rprint

from ..config import Config, get_config
from ..constants import prompt_assistant
from ..message import Message, MessageMetadata, format_msgs, len_tokens
from ..telemetry import trace_function
from ..tools import ToolSpec, ToolUse
from ..util import console
from .models import (
    MODELS,
    PROVIDERS_OPENAI,
    CustomProvider,
    ModelMeta,
    Provider,
    get_default_model_summary,
    get_model,
    is_custom_provider,
)
from .provider_plugins import (
    get_plugin_api_keys,
    get_provider_plugin,
    is_plugin_provider,
)

logger = logging.getLogger(__name__)


# Cheap/fast default model per provider for first-run / fallback scenarios.
# Azure is intentionally absent: deployments are tenant-specific, so there is
# no universal default — users must supply a full model name.
# Imported by gptme.cli.util and gptme.server.constants to avoid duplication.
PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "anthropic/claude-haiku-4-5",
    "openai": "openai/gpt-4o-mini",
    "openrouter": "openrouter/anthropic/claude-haiku-4-5",
    "gemini": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.1-8b-instant",
    "xai": "xai/grok-3-mini",
    "deepseek": "deepseek/deepseek-chat",
}

# Mapping from provider name to the environment variable that holds its API key.
# This is the single source of truth for provider authentication env vars.
PROVIDER_API_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


# Track subscription provider initialization
_subscription_initialized = False


# PEP 562 lazy attribute resolution for provider-specific functions.
# Preserves `from gptme.llm import init_anthropic` (and similar) for external
# callers without eagerly importing openai/anthropic at module load time.
_LAZY_PROVIDER_ATTRS = {
    "chat_anthropic": (".llm_anthropic", "chat"),
    "stream_anthropic": (".llm_anthropic", "stream"),
    "init_anthropic": (".llm_anthropic", "init"),
    "get_anthropic_client": (".llm_anthropic", "get_client"),
    "chat_openai": (".llm_openai", "chat"),
    "stream_openai": (".llm_openai", "stream"),
    "init_openai": (".llm_openai", "init"),
    "has_openai_client": (".llm_openai", "has_client"),
    "chat_subscription": (".llm_openai_subscription", "chat"),
    "stream_subscription": (".llm_openai_subscription", "stream"),
    "init_subscription": (".llm_openai_subscription", "init"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_PROVIDER_ATTRS:
        from importlib import import_module

        module_name, attr_name = _LAZY_PROVIDER_ATTRS[name]
        module = import_module(module_name, package=__name__)
        attr = getattr(module, attr_name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def init_llm(provider: Provider):
    """Initialize LLM client for a given provider if not already initialized.

    Args:
        provider: Provider name (built-in or custom)
    """
    from .llm_anthropic import get_client as get_anthropic_client
    from .llm_anthropic import init as init_anthropic
    from .llm_openai import has_client as has_openai_client
    from .llm_openai import init as init_openai
    from .llm_openai_subscription import init as init_subscription

    global _subscription_initialized
    config = get_config()

    # Check if it's a built-in OpenAI-compatible provider
    if provider in PROVIDERS_OPENAI and not has_openai_client(provider):
        init_openai(provider, config)
    # Check if it's a custom provider (OpenAI-compatible)
    elif is_custom_provider(provider) and not has_openai_client(provider):
        init_openai(provider, config)
    elif provider == "anthropic" and not get_anthropic_client():
        init_anthropic(config)
    elif provider == "openai-subscription" and not _subscription_initialized:
        _subscription_initialized = init_subscription(config)
    elif (
        plugin := get_provider_plugin(str(provider))
    ) is not None and not has_openai_client(provider):
        if plugin.init is not None:
            plugin.init(config)
            if not has_openai_client(provider):
                raise RuntimeError(
                    f"Plugin {plugin.name!r} init() did not register an "
                    "OpenAI-compatible client. Call "
                    "gptme.llm.llm_openai.init(provider, config) inside init()."
                )
        else:
            # Auto-init as OpenAI-compatible client (handled by llm_openai.init)
            init_openai(provider, config)
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
    on_token: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
) -> Message:
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens}")
    # Trigger GENERATION_PRE hooks and collect context messages
    from ..hooks import HookType, trigger_hook

    context_msgs = list(
        trigger_hook(
            HookType.GENERATION_PRE,
            messages,
            workspace=workspace,
            manager=None,
            model=model,
        )
    )

    # Add context messages for generation (don't modify original messages)
    generation_msgs = list(messages)  # Create a copy
    if context_msgs:
        generation_msgs.extend(context_msgs)

    init_llm(get_provider_from_model(model))
    config = get_config()
    agent_name = _get_agent_name(config)
    if on_token is not None and not stream:
        logger.warning("on_token callback has no effect when stream=False; ignoring")
    if stream:
        _env_break = config.get_env_bool("GPTME_BREAK_ON_TOOLUSE")
        if _env_break is not None:
            # Explicit env var overrides model-dependent default
            break_on_tooluse = _env_break
        else:
            # Default based on model capability: don't break for models that support
            # emitting multiple tool calls in a single response (e.g. Sonnet 4.6+)
            model_meta = get_model(model)
            break_on_tooluse = not model_meta.supports_parallel_tool_calls
        return _reply_stream(
            generation_msgs,
            model,
            tools,
            break_on_tooluse,
            agent_name=agent_name,
            output_schema=output_schema,
            on_token=on_token,
            max_tokens=max_tokens,
        )
    rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")
    response, metadata = _chat_complete(
        generation_msgs,
        model,
        tools,
        output_schema=output_schema,
        max_tokens=max_tokens,
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

    # Check plugin providers - also wrap in CustomProvider so OpenAI routing applies
    if is_plugin_provider(provider_str):
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
    max_tokens: int | None = None,
) -> tuple[str, MessageMetadata | None]:
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens}")
    provider = get_provider_from_model(model)

    # Providers with native constrained decoding support
    # Custom providers and plugin providers are OpenAI-compatible, route through OpenAI path
    if (
        provider in PROVIDERS_OPENAI
        or is_custom_provider(provider)
        or is_plugin_provider(str(provider))
    ):
        from .llm_openai import chat as chat_openai

        return chat_openai(
            messages, model, tools, output_schema=output_schema, max_tokens=max_tokens
        )
    if provider == "anthropic":
        from .llm_anthropic import chat as chat_anthropic

        return chat_anthropic(
            messages,
            _get_base_model(model),
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
        )
    if provider == "openai-subscription":
        from .llm_openai_subscription import chat as chat_subscription

        content = chat_subscription(
            messages, _get_base_model(model), tools, max_tokens=max_tokens
        )
        return content, {"model": model}

    # Unsupported provider - OpenAI and Anthropic are handled above
    raise ValueError(f"Unsupported provider: {provider}")


class _StreamWithMetadata:
    """Wrapper that captures a generator's return value (metadata).

    Metadata is returned by the provider generator as its return value
    (captured via StopIteration). When the stream is broken early (e.g.
    tool-use detection), we still populate the model name so messages
    always have at least basic metadata.
    """

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
        finally:
            # Ensure model is always set, even if the stream was broken early
            # (break_on_tooluse, KeyboardInterrupt) before the final chunk arrived
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
    max_tokens: int | None = None,
) -> _StreamWithMetadata:
    provider = get_provider_from_model(model)
    # Custom providers and plugin providers are OpenAI-compatible, route through OpenAI path
    if (
        provider in PROVIDERS_OPENAI
        or is_custom_provider(provider)
        or is_plugin_provider(str(provider))
    ):
        from .llm_openai import stream as stream_openai

        gen = stream_openai(
            messages, model, tools, output_schema=output_schema, max_tokens=max_tokens
        )
        return _StreamWithMetadata(gen, model)
    if provider == "anthropic":
        from .llm_anthropic import stream as stream_anthropic

        gen = stream_anthropic(
            messages,
            _get_base_model(model),
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
        )
        return _StreamWithMetadata(gen, model)
    if provider == "openai-subscription":
        from .llm_openai_subscription import stream as stream_subscription

        gen = stream_subscription(
            messages, _get_base_model(model), tools, max_tokens=max_tokens
        )
        return _StreamWithMetadata(gen, model)
    # Note: Validation-only fallback for streaming is complex
    # For now, unsupported providers don't support output_schema in streaming mode
    if output_schema is not None:
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
    on_token: Callable[[str], None] | None = None,
    max_tokens: int | None = None,
) -> Message:
    rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")

    def print_clear(length: int = 0):
        length = length or shutil.get_terminal_size().columns
        rprint("\r" + " " * length, end="\r")

    output = ""
    start_time = time.time()
    first_token_time = None
    are_thinking = False
    # Set to True when we detect a closing </think> tag so we can suppress the
    # one trailing blank "\n" that Anthropic always emits after </think>.
    just_closed_thinking = False
    # Buffer chars for the current line before forwarding to on_token.
    # Thinking-tag lines are only detectable at the '\n' that closes them, so we
    # must buffer the entire line and then decide whether to emit or suppress it.
    line_buffer: list[str] = []

    # Create stream wrapper to capture metadata
    stream = _stream(
        messages, model, tools, output_schema=output_schema, max_tokens=max_tokens
    )

    try:
        for char in (char for chunk in stream for char in chunk):
            if not output:  # first character
                first_token_time = time.time()
                print_clear()
                rprint(f"{prompt_assistant(agent_name)}: \n", end="")

            # Capture thinking state before the tag-detection update below so
            # we can tell whether a transition happened at this newline.
            prev_thinking = are_thinking

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
                    just_closed_thinking = True

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

            # Fire token callback (used by ACP path for incremental streaming).
            # We buffer each line and only flush to on_token once we confirm the
            # line is not a thinking-tag delimiter.  This prevents the opening
            # <think> tag characters from leaking to callers before are_thinking
            # flips at the trailing '\n'.
            if on_token:
                if char == "\n":
                    # A thinking-tag transition happened on this newline when
                    # are_thinking != prev_thinking.  In that case discard the
                    # buffer (tag line itself should not reach the caller).
                    if not are_thinking and not prev_thinking:
                        if line_buffer:
                            # Normal line — emit the whole line as one chunk.
                            on_token("".join(line_buffer) + "\n")
                        elif just_closed_thinking:
                            # Suppress the one blank "\n" that Anthropic always
                            # emits after "\n</think>\n\n".  Without this guard
                            # ACP clients would receive a spurious leading "\n"
                            # before the first real response character.
                            pass
                        else:
                            # Intentional blank line in response content.
                            on_token("\n")
                        # Only reset here (inside the normal-line branch), NOT on
                        # the "\n" that triggered the </think> detection — that "\n"
                        # has prev_thinking=True so it skips this block entirely,
                        # keeping just_closed_thinking set for the NEXT newline.
                        just_closed_thinking = False
                    line_buffer.clear()
                elif not are_thinking:
                    # Accumulate non-newline chars; we don't yet know if this
                    # line will turn out to be a thinking-tag opener.
                    line_buffer.append(char)
                # else: are_thinking is True — skip thinking content

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

        # Flush any remaining buffered chars (responses that end without a
        # trailing newline, or partial lines left after a break_on_tooluse break).
        if on_token and line_buffer and not are_thinking:
            on_token("".join(line_buffer))
            line_buffer.clear()

    except KeyboardInterrupt:
        # Flush partial line before the interrupt suffix so callers see the
        # content that was streamed up to the interrupt point.
        if on_token and line_buffer:
            on_token("".join(line_buffer))
            line_buffer.clear()
        suffix = "... ^C Interrupted"
        if on_token:
            # Emit as one chunk; ACP batching callback handles downstream chunking.
            on_token(suffix)
        return Message("assistant", output + suffix, metadata=stream.metadata)
    finally:
        # Explicitly close the underlying generator to release resources
        # This handles all exit paths: normal completion, KeyboardInterrupt, and tool break
        stream.gen.close()
        print_clear()
        if first_token_time:
            end_time = time.time()
            gen_time = max(
                end_time - first_token_time, 0.001
            )  # Prevent division by zero
            logger.debug(
                f"Generation finished in {end_time - start_time:.1f}s "
                f"(ttft: {first_token_time - start_time:.2f}s, "
                f"gen: {gen_time:.2f}s, "
                f"tok/s: {len_tokens(output, model) / gen_time:.1f})"
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
    if not model:
        raise RuntimeError("No default model set")

    if len_tokens(messages, model.model) > model.context:
        raise ValueError(
            f"Cannot summarize more than {model.context} tokens, got {len_tokens(messages, model.model)}"
        )

    summary, _metadata = _chat_complete(messages, model.full, None)
    if not summary:
        raise RuntimeError("Summarization produced no output")
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
    List all available providers based on configured API keys or OAuth tokens.

    Returns:
        List of tuples (provider, auth_source) for configured providers.
        auth_source is an env var name for API key providers, or "oauth" for
        OAuth-based providers like openai-subscription.
    """
    config = get_config()
    available = []

    for provider, env_var in PROVIDER_API_KEYS.items():
        if config.get_env(env_var):
            available.append((cast(Provider, provider), env_var))

    # Check OAuth-based providers (no API key, use token file)
    # Note: compute path directly to avoid side-effecting mkdir in _get_token_storage_path()
    _config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    _token_path = _config_dir / "gptme" / "oauth" / "openai_subscription.json"
    if _token_path.exists():
        available.append((cast(Provider, "openai-subscription"), "oauth"))

    # Include plugin providers that have their API key configured
    for plugin_name, env_var in get_plugin_api_keys().items():
        if config.get_env(env_var):
            available.append((CustomProvider(plugin_name), env_var))

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
    if api_key.startswith("sk-or-"):
        return api_key, "openrouter", "OPENROUTER_API_KEY"
    if api_key.startswith("sk-"):
        return api_key, "openai", "OPENAI_API_KEY"
    if api_key.startswith("AIza"):
        return api_key, "gemini", "GEMINI_API_KEY"

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
    raise ValueError(f"Provider {provider} does not support listing models")
