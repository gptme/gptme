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
from ..message import (
    Message,
    MessageMetadata,
    format_msgs,
    is_output_json,
    is_output_quiet,
    len_tokens,
)
from ..telemetry import trace_function
from ..tools import ToolSpec, ToolUse, get_tool_format
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
_max_tokens_env_warned = False


def _tooluse_break_check_char(char: str) -> bool:
    """Return whether this character can complete a runnable tool call."""
    current_format = get_tool_format()
    if current_format == "markdown":
        return char == "\n"
    if current_format == "tool":
        return char in {"}", "\n"}
    if current_format == "xml":
        return char in {">", "\n"}
    return char == "\n"


def _drain_toolbreak_stream(stream: "_StreamWithMetadata") -> None:
    """Drain the generator after a break_on_tooluse to capture message_delta.

    When break_on_tooluse fires before message_delta, the output tokens and
    cost ($0.0000) are lost from the per-step breakdown.  Draining the
    remaining generator processes the pending events (including message_delta)
    so the metadata is complete.

    This is safe because after the tool-use break, we discard the drained
    content — it's only the side-effects (metadata capture) we care about.
    """
    discarded = 0
    try:
        while True:
            next(stream.gen)
            discarded += 1
    except StopIteration as e:
        # Capture the metadata that the provider generator set as its
        # return value (e.g. Anthropic message_delta usage, OpenAI
        # response.completed usage).  This overwrites the partial fallback
        # (message_start only has input/cache counts) so the per-step
        # breakdown shows accurate output tokens and cost.
        if e.value:
            stream.metadata = e.value
    if discarded:
        logger.debug("Drained %d chunks after tool-use break", discarded)


# Cheap/fast default model per provider for first-run / fallback scenarios.
# Azure is intentionally absent: deployments are tenant-specific, so there is
# no universal default — users must supply a full model name.
# Imported by gptme.cli.util and gptme.server.constants to avoid duplication.
PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "anthropic/claude-haiku-4-5",
    "openai": "openai/gpt-4o-mini",
    "openrouter": "openrouter/anthropic/claude-haiku-4.5",
    "requesty": "requesty/openai/gpt-4o-mini",
    "gemini": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.3-70b-versatile",
    "xai": "xai/grok-3-mini",
    "grok-subscription": "grok-subscription/grok-4.5",
    "deepseek": "deepseek/deepseek-chat",
    "moonshot": "moonshot/kimi-k2.6",
}

# Mapping from provider name to the environment variable that holds its API key.
# This is the single source of truth for provider authentication env vars.
PROVIDER_API_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "requesty": "REQUESTY_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
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
    global _subscription_initialized
    config = get_config()

    # Provider modules are imported lazily per-branch: each pulls in its SDK
    # (anthropic/openai, several hundred ms), so only the active provider's
    # module should be imported at startup.
    def has_openai_client(p) -> bool:
        from .llm_openai import has_client

        return has_client(p)

    def init_openai(p, conf) -> None:
        from .llm_openai import init

        init(p, conf)

    # Mock provider needs no client/auth — responses are computed in-process.
    if provider == "mock":
        return
    # Check if it's a built-in OpenAI-compatible provider
    if provider in PROVIDERS_OPENAI and not has_openai_client(provider):
        init_openai(provider, config)
    # Check if it's a custom provider (OpenAI-compatible)
    elif is_custom_provider(provider) and not has_openai_client(provider):
        init_openai(provider, config)
    elif provider == "anthropic":
        from .llm_anthropic import get_client as get_anthropic_client
        from .llm_anthropic import init as init_anthropic

        if not get_anthropic_client():
            init_anthropic(config)
    elif provider == "openai-subscription" and not _subscription_initialized:
        from .llm_openai_subscription import init as init_subscription

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


# OpenRouter default: 16k is enough headroom for most coding/eval responses
# while still reducing the credit reservation from model.max_output (up to
# 64k) to a value that works with partially-spent daily-budget keys.
# Override with GPTME_MAX_TOKENS for larger responses.
_OPENROUTER_DEFAULT_MAX_TOKENS = 16000


def _resolve_max_tokens(model: str, max_tokens: int | None) -> int | None:
    """Apply the GPTME_MAX_TOKENS env override or a sane default for providers that require it.

    When max_tokens is omitted, OpenRouter reserves model.max_output +
    reasoning_budget credits per request. For large models like Sonnet 4.6
    that is ~65k tokens. A partially-spent daily-budget key rejects this
    with HTTP 402 even when the actual response would fit comfortably.

    The gptme cloud provider (Supabase messages function) requires max_tokens
    explicitly — omitting it returns HTTP 400 Field required.

    Setting a default max_tokens reduces the reservation so partial-budget
    keys still work.
    """

    if max_tokens is not None:
        return max_tokens

    provider = get_provider_from_model(model)

    # Only apply defaults for providers that need them
    if provider not in ("openrouter", "gptme"):
        return None

    raw = get_config().get_env("GPTME_MAX_TOKENS")
    if raw is not None and raw != "":
        global _max_tokens_env_warned
        try:
            parsed = int(raw)
        except ValueError:
            if not _max_tokens_env_warned:
                logger.warning("Invalid GPTME_MAX_TOKENS value: %r, ignoring", raw)
                _max_tokens_env_warned = True
        else:
            if parsed > 0:
                return parsed
            if not _max_tokens_env_warned:
                logger.warning(
                    "GPTME_MAX_TOKENS must be a positive integer, got %r; ignoring",
                    raw,
                )
                _max_tokens_env_warned = True

    if provider == "openrouter":
        return _OPENROUTER_DEFAULT_MAX_TOKENS

    # provider == "gptme": use max_output from model metadata, with 4096 fallback
    from .models import get_model  # fmt: skip

    model_meta = get_model(model)
    return model_meta.max_output or 4096


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
    temperature: float | None = None,
    top_p: float | None = None,
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
            temperature=temperature,
            top_p=top_p,
        )
    json_mode = is_output_json()
    if not json_mode and not is_output_quiet():
        rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")
    response, metadata = _chat_complete(
        generation_msgs,
        model,
        tools,
        output_schema=output_schema,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )
    if not json_mode and not is_output_quiet():
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
    """Get base model name without provider prefix.

    No alias rewriting here: aliases in MODEL_ALIASES are accepted by the
    providers' APIs (e.g. OpenAI serves gpt-5.6 as gpt-5.6-sol, verified
    2026-07-10), so the requested name is sent as-is. Aliases are resolved
    for metadata lookup only, in models/resolution.py.
    """
    return model.split("/", 1)[1]


def _gptme_backend(model: str) -> tuple[str, str] | None:
    """For a gptme cloud model, return (backend_provider, bare_model).

    gptme cloud models carry their real backend as a prefix in the resolved
    ModelMeta.model (e.g. "anthropic/claude-sonnet-4-6", "openai/gpt-5",
    "openrouter/openai/gpt-5.4"). This recovers that backend so a request can be
    routed through the right SDK. Returns None for non-gptme models or when the
    backend can't be determined (degraded/offline resolution).
    """
    from .models import get_model  # fmt: skip

    if get_provider_from_model(model) != "gptme":
        return None
    meta = get_model(model)
    if str(meta.provider) != "gptme" or "/" not in meta.model:
        return None
    backend, bare = meta.model.split("/", 1)
    if backend not in ("anthropic", "openai", "openrouter"):
        return None
    return backend, bare


@trace_function(name="llm.chat_complete", attributes={"component": "llm"})
def _chat_complete(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> tuple[str, MessageMetadata | None]:
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens}")
    max_tokens = _resolve_max_tokens(model, max_tokens)
    provider = get_provider_from_model(model)

    # Anthropic-backed gptme cloud models must use the Anthropic SDK (native
    # format) — the gateway forwards the body verbatim to Anthropic, which
    # rejects the OpenAI-only `stream_options`. openai/openrouter-backed gptme
    # models stay on the OpenAI path below (the gateway forwards to those APIs,
    # which accept the OpenAI request shape).
    if (backend := _gptme_backend(model)) and backend[0] == "anthropic":
        from .llm_anthropic import chat as chat_anthropic

        return chat_anthropic(
            messages,
            backend[1],
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            via_gptme=True,
        )

    # grok-subscription: refresh the cached OpenAI client if the token is
    # about to expire before delegating to the shared OpenAI chat path.
    # This mirrors the same guard in _stream() so non-streaming requests
    # don't keep sending an expired token and receiving 401 responses.
    if provider == "grok-subscription":
        from .llm_grok_subscription import GROK_CLIENT_VERSION, GROK_PROXY_URL, get_auth
        from .llm_openai import _init_openai_client

        try:
            auth = get_auth()
            _init_openai_client(
                "grok-subscription",
                api_key=auth.access_token,
                base_url=GROK_PROXY_URL,
                default_headers={"x-grok-client-version": GROK_CLIENT_VERSION},
            )
        except Exception:
            pass  # let chat_openai surface the auth error naturally

    # Custom providers and plugin providers are OpenAI-compatible, route through OpenAI path
    if (
        provider in PROVIDERS_OPENAI
        or is_custom_provider(provider)
        or is_plugin_provider(str(provider))
    ):
        from .llm_openai import chat as chat_openai

        return chat_openai(
            messages,
            model,
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "anthropic":
        from .llm_anthropic import chat as chat_anthropic

        return chat_anthropic(
            messages,
            _get_base_model(model),
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
    if provider == "openai-subscription":
        from .llm_openai_subscription import chat as chat_subscription

        content = chat_subscription(
            messages, _get_base_model(model), tools, max_tokens=max_tokens
        )
        return content, {"model": model}
    if provider == "mock":
        from .llm_mock import chat as chat_mock

        return chat_mock(
            messages,
            model,
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

    # Unsupported provider - OpenAI and Anthropic are handled above
    raise ValueError(f"Unsupported provider: {provider}")


class _StreamWithMetadata:
    """Wrapper that captures a generator's return value (metadata).

    Metadata is returned by the provider generator as its return value
    (captured via StopIteration). When the stream is broken early (e.g.
    tool-use detection), we fall back to partial metadata captured from the
    provider's early events (e.g. Anthropic's message_start which carries
    input/cache token counts before the model starts generating).
    """

    def __init__(
        self,
        gen: Generator[str, None, MessageMetadata | None],
        model: str,
        partial: dict | None = None,
    ):
        self.gen = gen
        self.model = model
        self.metadata: MessageMetadata | None = None
        # Optional dict populated by the provider generator with partial usage
        # from early stream events (e.g. message_start).  Used as fallback when
        # the stream is closed before the final usage event arrives.
        self._partial = partial

    def __iter__(self) -> Iterator[str]:
        try:
            while True:
                yield next(self.gen)
        except StopIteration as e:
            if self.metadata is None:
                # Only capture from StopIteration if the drain didn't already
                # set it.  _drain_toolbreak_stream consumes the generator and
                # sets metadata from message_delta / response.completed, but
                # the exhausted generator then raises StopIteration a second
                # time with value=None — don't overwrite the rich metadata.
                self.metadata = e.value
        finally:
            if self.metadata is None:
                # Fall back to partial metadata captured from message_start if
                # the stream was closed before message_delta (break_on_tooluse).
                partial_meta: MessageMetadata | None = (
                    self._partial.get("metadata") if self._partial else None
                )
                self.metadata = partial_meta or {"model": self.model}
            elif "model" not in self.metadata:
                self.metadata["model"] = self.model


@trace_function(name="llm.stream", attributes={"component": "llm"})
def _stream(
    messages: list[Message],
    model: str,
    tools: list[ToolSpec] | None,
    output_schema: type | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
) -> _StreamWithMetadata:
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens}")
    max_tokens = _resolve_max_tokens(model, max_tokens)
    provider = get_provider_from_model(model)

    # Anthropic-backed gptme cloud models use the Anthropic SDK (native format);
    # see the matching note in _chat_complete.
    if (backend := _gptme_backend(model)) and backend[0] == "anthropic":
        from .llm_anthropic import stream as stream_anthropic

        gptme_partial: dict = {}
        gen = stream_anthropic(
            messages,
            backend[1],
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            _partial=gptme_partial,
            via_gptme=True,
        )
        return _StreamWithMetadata(gen, model, partial=gptme_partial)

    # grok-subscription: refresh the cached OpenAI client if the token is
    # about to expire before delegating to the shared OpenAI stream path.
    # Without this, a long-running CLI or server reuses a stale client and
    # receives 401 responses until the process is restarted.
    if provider == "grok-subscription":
        from .llm_grok_subscription import GROK_CLIENT_VERSION, GROK_PROXY_URL, get_auth
        from .llm_openai import _init_openai_client

        try:
            auth = get_auth()
            _init_openai_client(
                "grok-subscription",
                api_key=auth.access_token,
                base_url=GROK_PROXY_URL,
                default_headers={"x-grok-client-version": GROK_CLIENT_VERSION},
            )
        except Exception:
            pass  # let stream_openai surface the auth error naturally

    # Custom providers and plugin providers are OpenAI-compatible, route through OpenAI path
    if (
        provider in PROVIDERS_OPENAI
        or is_custom_provider(provider)
        or is_plugin_provider(str(provider))
    ):
        from .llm_openai import stream as stream_openai

        gen = stream_openai(
            messages,
            model,
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return _StreamWithMetadata(gen, model)
    if provider == "anthropic":
        from .llm_anthropic import stream as stream_anthropic

        # Shared dict: Anthropic generator writes partial usage from
        # message_start; _StreamWithMetadata reads it as a fallback when the
        # stream is closed before message_delta (break_on_tooluse path).
        partial: dict = {}
        gen = stream_anthropic(
            messages,
            _get_base_model(model),
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            _partial=partial,
        )
        return _StreamWithMetadata(gen, model, partial=partial)
    if provider == "openai-subscription":
        from .llm_openai_subscription import stream as stream_subscription

        gen = stream_subscription(
            messages, _get_base_model(model), tools, max_tokens=max_tokens
        )
        return _StreamWithMetadata(gen, model)
    if provider == "mock":
        from .llm_mock import stream as stream_mock

        gen = stream_mock(
            messages,
            model,
            tools,
            output_schema=output_schema,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
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
    temperature: float | None = None,
    top_p: float | None = None,
) -> Message:
    json_mode = is_output_json()
    quiet_mode = is_output_quiet()
    display_enabled = not json_mode and not quiet_mode
    if display_enabled:
        rprint(f"{prompt_assistant(agent_name)}: Thinking...", end="\r")

    def print_clear(length: int = 0):
        if not display_enabled:
            return
        length = length or shutil.get_terminal_size().columns
        rprint("\r" + " " * length, end="\r")

    # Combine the on_token callback (ACP/display) with generation.chunk hooks
    # (e.g. streaming TTS) into a single emitter, so the per-line buffering below
    # runs whenever either consumer is interested — not just when on_token is set.
    from ..hooks import HookType, get_hooks, trigger_hook

    _chunk_hooks_active = bool(get_hooks(HookType.GENERATION_CHUNK))
    emit_active = on_token is not None or _chunk_hooks_active

    def _emit_chunk(text: str) -> None:
        if _chunk_hooks_active:
            for _ in trigger_hook(HookType.GENERATION_CHUNK, chunk=text):
                pass
        if on_token is not None:
            on_token(text)

    output = ""
    start_time = time.time()
    first_token_time = None
    are_thinking = False
    # Set to True when we detect a closing </think> tag so we can suppress the
    # one trailing blank "\n" that Anthropic always emits after </think>.
    just_closed_thinking = False
    # True while inside a multiline <!-- think-sig: ... --> comment.
    # Only the first line starts with "<!-- think-sig:"; continuation lines and
    # the closing "-->" are also suppressed until the comment ends.
    in_think_sig = False
    # Buffer chars for the current line before forwarding to on_token.
    # Thinking-tag lines are only detectable at the '\n' that closes them, so we
    # must buffer the entire line and then decide whether to emit or suppress it.
    line_buffer: list[str] = []
    # Buffer chars inside thinking blocks for display purposes.
    # We emit per-line rather than per-char so that long think-sig lines (hundreds
    # of base64 chars) never get printed to the terminal: printing char-by-char and
    # then calling print_clear() only erases the current terminal row, leaving
    # already-wrapped rows visible.  By buffering and emitting at the newline we
    # can simply discard think-sig lines without any terminal-clear gymnastics.
    think_display_buffer: list[str] = []
    # Buffer for normal (non-thinking) chars accumulated within a stream chunk.
    # Flushed at chunk boundaries and before each newline.  Reduces rprint() and
    # sys.stdout.flush() calls from O(chars) to O(chunks) — the main source of
    # bursty terminal rendering (gptme/gptme#2717 terminal side).
    normal_display_buffer: list[str] = []

    # Create stream wrapper to capture metadata
    stream = _stream(
        messages,
        model,
        tools,
        output_schema=output_schema,
        max_tokens=max_tokens,
        temperature=temperature,
        top_p=top_p,
    )

    def _chars_with_chunk_end(text_chunks):
        """Yield (char, is_last_in_chunk) — lets the loop flush once per chunk."""
        for chunk in text_chunks:
            n = len(chunk)
            for i, c in enumerate(chunk):
                yield c, i == n - 1

    try:
        for char, _is_chunk_end in _chars_with_chunk_end(stream):
            if not output:  # first character
                first_token_time = time.time()
                print_clear()
                if display_enabled:
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
                    # Print spaces to clear the line (in case chars were
                    # already flushed to the terminal at a chunk boundary).
                    print_clear(len(last_line))
                    # Discard buffered tag chars so they aren't flushed again
                    # at the newline below (mirrors think_display_buffer.clear()
                    # done for the closing tag).
                    normal_display_buffer.clear()
                    # Print styled version
                    if display_enabled:
                        rprint(f"[dim]{last_line}[/dim]", end="")
                    are_thinking = True
                # Check for closing tag
                elif last_line == "</think>" or last_line == "</thinking>":
                    # Chars were buffered in think_display_buffer, not printed;
                    # no print_clear needed.
                    think_display_buffer.clear()
                    if display_enabled:
                        rprint(f"[dim]{last_line}[/dim]", end="")
                    are_thinking = False
                    in_think_sig = False
                    just_closed_thinking = True
                # Suppress Anthropic think-sig comment from display.
                # The comment can span multiple lines:
                #   <!-- think-sig: base64...   ← first line (starts the comment)
                #   continuation_base64...       ← suppressed by in_think_sig
                #   more_base64... -->           ← last line (ends the comment)
                # The line is still accumulated in `output` so the signature
                # survives message serialisation and API round-tripping.
                elif are_thinking and last_line.startswith("<!-- think-sig:"):
                    in_think_sig = not last_line.endswith("-->")
                    # Chars were buffered in think_display_buffer, not printed;
                    # just discard — no print_clear needed.
                    think_display_buffer.clear()
                    output += char
                    continue
                elif in_think_sig:
                    # Continuation or closing line of a multiline think-sig comment.
                    if last_line.endswith("-->"):
                        in_think_sig = False
                    # in_think_sig chars are suppressed (not buffered), so
                    # think_display_buffer is already empty; clear defensively.
                    think_display_buffer.clear()
                    output += char
                    continue

                # Now print the newline, flushing any buffered content first.
                if display_enabled:
                    if normal_display_buffer:
                        # Flush buffered normal chars before the newline so the
                        # line content appears before the line ending.
                        rprint("".join(normal_display_buffer), end="")
                        normal_display_buffer.clear()
                    if think_display_buffer:
                        # Emit the entire buffered line dimly in one shot.
                        rprint(f"[dim]{''.join(think_display_buffer)}[/dim]", end="")
                        think_display_buffer.clear()
                    rprint(char, end="")
            else:
                # Print normal characters
                if display_enabled:
                    if in_think_sig:
                        pass  # suppress think-sig comment body chars
                    elif are_thinking:
                        # Buffer instead of printing char-by-char.  Per-char dim
                        # printing causes terminal-wrap issues for long lines
                        # (print_clear only erases the current row, leaving
                        # already-wrapped rows visible when think-sig is detected).
                        think_display_buffer.append(char)
                    else:
                        # Accumulate in normal_display_buffer; flushed at chunk
                        # boundaries and before each newline.  This reduces
                        # rprint() calls from O(chars) to O(chunks).
                        normal_display_buffer.append(char)

            assert len(char) == 1
            output += char

            # Fire token callback (used by ACP path for incremental streaming).
            # We buffer each line and only flush to on_token once we confirm the
            # line is not a thinking-tag delimiter.  This prevents the opening
            # <think> tag characters from leaking to callers before are_thinking
            # flips at the trailing '\n'.
            if emit_active:
                if char == "\n":
                    # A thinking-tag transition happened on this newline when
                    # are_thinking != prev_thinking.  In that case discard the
                    # buffer (tag line itself should not reach the caller).
                    if not are_thinking and not prev_thinking:
                        if line_buffer:
                            # Normal line — emit the whole line as one chunk.
                            _emit_chunk("".join(line_buffer) + "\n")
                        elif just_closed_thinking:
                            # Suppress the one blank "\n" that Anthropic always
                            # emits after "\n</think>\n\n".  Without this guard
                            # ACP clients would receive a spurious leading "\n"
                            # before the first real response character.
                            pass
                        else:
                            # Intentional blank line in response content.
                            _emit_chunk("\n")
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

            # Flush buffered normal chars and sync stdout once per chunk.
            # Moving flush from O(chars) to O(chunks) eliminates the main source
            # of bursty terminal rendering (per-char syscall overhead).
            if _is_chunk_end and display_enabled:
                if normal_display_buffer:
                    rprint("".join(normal_display_buffer), end="")
                    normal_display_buffer.clear()
                sys.stdout.flush()

            # Trigger tool detection at cheap format-specific breakpoints.
            # Markdown tools need line completion; XML/tool formats can finish
            # before a trailing newline, so waiting only for "\n" can leak
            # extra assistant text past the breakpoint.
            if break_on_tooluse and _tooluse_break_check_char(char):
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
                    # Drain remaining stream to capture message_delta (output tokens).
                    # When break_on_tooluse fires before message_delta, the
                    # _StreamWithMetadata fallback only has input/cache counts from
                    # message_start — output tokens and cost are lost.
                    _drain_toolbreak_stream(stream)
                    break

        # Flush any remaining buffered chars (responses that end without a
        # trailing newline, or partial lines left after a break_on_tooluse break).
        if display_enabled and normal_display_buffer:
            rprint("".join(normal_display_buffer), end="")
            normal_display_buffer.clear()
        if display_enabled and think_display_buffer:
            rprint(f"[dim]{''.join(think_display_buffer)}[/dim]", end="")
            think_display_buffer.clear()
        if emit_active and line_buffer and not are_thinking:
            _emit_chunk("".join(line_buffer))
            line_buffer.clear()

    except KeyboardInterrupt:
        # Flush any chars buffered since the last chunk boundary so the terminal
        # shows everything received before the interrupt.
        if display_enabled and normal_display_buffer:
            rprint("".join(normal_display_buffer), end="")
            normal_display_buffer.clear()
        # Flush partial line before the interrupt suffix so callers see the
        # content that was streamed up to the interrupt point.
        if emit_active and line_buffer:
            _emit_chunk("".join(line_buffer))
            line_buffer.clear()
        suffix = "... ^C Interrupted"
        if on_token:
            # Emit as one chunk; ACP batching callback handles downstream chunking.
            # Display/ACP only — not a chunk hook event (don't speak the suffix).
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
    from ..credentials import STORED_CREDENTIALS_SOURCE, list_stored_credentials

    config = get_config()
    available = []
    seen: set[str] = set()

    for provider, env_var in PROVIDER_API_KEYS.items():
        if config.get_env(env_var):
            available.append((cast(Provider, provider), env_var))
            seen.add(provider)

    for provider, _api_key in list_stored_credentials():
        if provider not in seen:
            available.append((cast(Provider, provider), STORED_CREDENTIALS_SOURCE))
            seen.add(provider)

    # Check OAuth-based providers (no API key, use token file)
    # Note: compute path directly to avoid side-effecting mkdir in _get_token_storage_path()
    _config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    _token_path = _config_dir / "gptme" / "oauth" / "openai_subscription.json"
    if _token_path.exists() and "openai-subscription" not in seen:
        available.append((cast(Provider, "openai-subscription"), "oauth"))
        seen.add("openai-subscription")

    # Grok subscription: check both gptme-stored tokens and grok CLI auth file
    _grok_token_path = _config_dir / "gptme" / "oauth" / "grok_subscription.json"
    _grok_cli_path = Path.home() / ".grok" / "auth.json"
    if (
        _grok_token_path.exists() or _grok_cli_path.exists()
    ) and "grok-subscription" not in seen:
        available.append((cast(Provider, "grok-subscription"), "oauth"))
        seen.add("grok-subscription")

    # Include plugin providers that have their API key configured
    for plugin_name, env_var in get_plugin_api_keys().items():
        if config.get_env(env_var) and plugin_name not in seen:
            available.append((CustomProvider(plugin_name), env_var))
            seen.add(plugin_name)

    # Note: "mock" is intentionally absent here. It requires no credentials and
    # is always usable when explicitly requested (e.g. "mock/echo"). Surfacing it
    # in credential discovery would make it a candidate for auto-selection in
    # environments with no real API keys, which is not the intended use-case.
    # Users who want mock must specify it explicitly.

    return available


def guess_provider_from_config() -> Provider | None:
    """
    Guess the provider to use from the configuration.
    """
    available = list_available_providers()
    if available:
        provider, _ = available[0]  # Return first available provider
        if not is_output_json():
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
    if provider in ("openrouter", "local", "gptme") or is_custom_provider(provider):
        from .llm_openai import get_available_models as get_openai_models

        return get_openai_models(provider)
    raise ValueError(f"Provider {provider} does not support listing models")
