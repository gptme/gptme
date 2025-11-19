import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import (
    Literal,
    TypedDict,
    cast,
    get_args,
)

from typing_extensions import NotRequired

from .llm_openai_models import OPENAI_MODELS

logger = logging.getLogger(__name__)

# available providers
Provider = Literal[
    "openai",
    "anthropic",
    "azure",
    "openrouter",
    "gemini",
    "groq",
    "xai",
    "deepseek",
    "nvidia",
    "local",
]
PROVIDERS: list[Provider] = cast(list[Provider], get_args(Provider))
PROVIDERS_OPENAI: list[Provider]
PROVIDERS_OPENAI = [
    "openai",
    "azure",
    "openrouter",
    "gemini",
    "xai",
    "groq",
    "deepseek",
    "nvidia",
    "local",
]


@dataclass(frozen=True)
class ModelMeta:
    provider: Provider | Literal["unknown"]
    model: str
    context: int
    max_output: int | None = None
    supports_streaming: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False  # models which support reasoning do not need prompting to use <thinking> tags

    # price in USD per 1M tokens
    # if price is not set, it is assumed to be 0
    price_input: float = 0
    price_output: float = 0

    knowledge_cutoff: datetime | None = None

    @property
    def full(self) -> str:
        return f"{self.provider}/{self.model}"


class _ModelDictMeta(TypedDict):
    context: int
    max_output: NotRequired[int]

    # price in USD per 1M tokens
    price_input: NotRequired[float]
    price_output: NotRequired[float]

    supports_streaming: NotRequired[bool]
    supports_vision: NotRequired[bool]
    supports_reasoning: NotRequired[bool]

    knowledge_cutoff: NotRequired[datetime]


# default model - using ContextVar for thread safety
_default_model_var: ContextVar[ModelMeta | None] = ContextVar(
    "default_model", default=None
)

# known models metadata
# TODO: can we get this from the API?
MODELS: dict[Provider, dict[str, _ModelDictMeta]] = {
    "openai": OPENAI_MODELS,
    # https://docs.anthropic.com/en/docs/about-claude/models
    "anthropic": {
        "claude-sonnet-4-5": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2025, 7, 1),
        },
        "claude-haiku-4-5": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 1,
            "price_output": 5,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2025, 2, 1),
        },
        "claude-opus-4-1-20250805": {
            "context": 200_000,
            "max_output": 32_000,
            "price_input": 15,
            "price_output": 75,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2025, 3, 1),
        },
        "claude-opus-4-20250514": {
            "context": 200_000,
            "max_output": 32_000,
            "price_input": 15,
            "price_output": 75,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2025, 3, 1),
        },
        "claude-sonnet-4-20250514": {
            "context": 200_000,
            "max_output": 64_000,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2025, 3, 1),
        },
        "claude-3-7-sonnet-20250219": {
            "context": 200_000,
            # TODO: supports beta header `output-128k-2025-02-19` for 128k output option
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "supports_reasoning": True,
            "knowledge_cutoff": datetime(2024, 10, 1),
        },
        "claude-3-5-sonnet-20241022": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-5-sonnet-20240620": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 3,
            "price_output": 15,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-5-haiku-20241022": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 1,
            "price_output": 5,
            "supports_vision": True,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-haiku-20240307": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 0.25,
            "price_output": 1.25,
            "knowledge_cutoff": datetime(2024, 4, 1),
        },
        "claude-3-opus-20240229": {
            "context": 200_000,
            "max_output": 4096,
            "price_input": 15,
            "price_output": 75,
        },
    },
    # https://ai.google.dev/gemini-api/docs/models/gemini#gemini-1.5-flash
    # https://ai.google.dev/pricing#1_5flash
    "gemini": {
        "gemini-1.5-flash-latest": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
        },
        "gemini-2.0-flash-thinking-exp-01-21": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            "price_output": 0.60,
            "supports_vision": True,
        },
        "gemini-2.0-flash-lite": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.075,
            "price_output": 0.30,
        },
        "gemini-2.5-flash-preview-04-17": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.15,
            # NOTE: $3.5/Mtok for thinking tokens
            "price_output": 0.60,
            "supports_vision": True,
        },
        "gemini-2.5-pro-preview-05-06": {
            "context": 1_048_576,
            "max_output": 8192,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
        },
        "gemini-2.5-flash-lite": {
            "context": 1_000_000,
            "max_output": 64_000,
            "price_input": 0.1,
            "price_output": 0.4,
            "supports_vision": True,
        },
        "gemini-2.5-flash": {
            "context": 1_048_576,
            "max_output": 65_536,
            "price_input": 0.3,
            "price_output": 2.5,
            "supports_vision": True,
        },
        "gemini-2.5-pro": {
            "context": 1_048_576,
            "max_output": 8192,
            # NOTE: at >200k context price is 2x for input and 1.5x for output
            "price_input": 1.25,
            "price_output": 10,
            "supports_vision": True,
        },
    },
    # https://api-docs.deepseek.com/quick_start/pricing
    "deepseek": {
        "deepseek-chat": {
            "context": 128_000,
            "max_output": 8192,
            # 10x better price for cache hits
            "price_input": 0.14,
            "price_output": 1.1,
        },
        "deepseek-reasoner": {
            "context": 128_000,
            "max_output": 8192,
            "price_input": 0.55,
            "price_output": 2.19,
        },
    },
    # https://groq.com/pricing/
    "groq": {
        "llama-3.3-70b-versatile": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.59,
            "price_output": 0.79,
        },
    },
    "xai": {
        "grok-beta": {
            "context": 131_072,
            "max_output": 4096,  # guess
            "price_input": 5,
            "price_output": 15,
        },
        "grok-vision-beta": {
            "context": 8192,
            "max_output": 4096,  # guess
            "price_input": 5,  # $10/1Mtok for vision
            "price_output": 15,
            "supports_vision": True,
        },
    },
    "openrouter": {
        "qwen/qwen3-max": {
            "context": 256_000,
            "max_output": 8192,
            "price_input": 1.2,
            "price_output": 6.0,
            "supports_vision": True,
        },
        "mistralai/magistral-medium-2506": {
            "context": 41_000,
            "max_output": 40_000,
            "price_input": 2,
            "price_output": 5,
            # "supports_vision": True,
            "supports_reasoning": True,
        },
        "anthropic/claude-3.5-sonnet": {
            "context": 200_000,
            "max_output": 8192,
            "price_input": 3,
            "price_output": 15,
            "supports_vision": True,
        },
        "meta-llama/llama-3.3-70b-instruct": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.12,
            "price_output": 0.3,
        },
        "meta-llama/llama-3.1-405b-instruct": {
            "context": 128_000,
            "max_output": 32_768,
            "price_input": 0.8,
            "price_output": 0.8,
        },
        "google/gemini-flash-1.5": {
            "context": 1_048_576,
            "max_output": 8192,
            "price_input": 0.075,
            "price_output": 0.3,
            "supports_vision": True,
        },
        "moonshotai/kimi-k2": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
        },
        "moonshotai/kimi-k2-0905": {
            "context": 262_144,
            "max_output": 262_144,
            "price_input": 0.38,
            "price_output": 1.52,
            "supports_vision": True,
        },
    },
    "nvidia": {},
    "azure": {},
    "local": {},
}

# check that all providers have a MODELS entry
assert set(PROVIDERS) == set(MODELS.keys())


def get_default_model() -> ModelMeta | None:
    return _default_model_var.get()


def get_default_model_summary() -> ModelMeta | None:
    default_model = get_default_model()
    if not default_model:
        return None
    provider = default_model.provider
    assert provider != "unknown"
    return get_model(f"{provider}/{get_summary_model(provider)}")


def set_default_model(model: str | ModelMeta) -> None:
    modelmeta = model if isinstance(model, ModelMeta) else get_model(model)
    assert modelmeta
    _default_model_var.set(modelmeta)


_logged_warnings = set()


def log_warn_once(msg: str):
    if msg not in _logged_warnings:
        logger.warning(msg)
        _logged_warnings.add(msg)


def get_model(model: str) -> ModelMeta:
    # if only provider is given, get recommended model
    if model in PROVIDERS:
        provider = cast(Provider, model)
        model = get_recommended_model(provider)
        return get_model(f"{provider}/{model}")

    # Check if model has provider/model format
    if any(model.startswith(f"{provider}/") for provider in PROVIDERS):
        provider_str, model_name = model.split("/", 1)

        # Check if provider is known
        if provider_str in PROVIDERS:
            provider = cast(Provider, provider_str)

            # First try static MODELS dict for performance
            if provider in MODELS and model_name in MODELS[provider]:
                return ModelMeta(provider, model_name, **MODELS[provider][model_name])

            # For providers that support dynamic fetching, use _get_models_for_provider
            if provider == "openrouter":
                try:
                    models = _get_models_for_provider(provider, dynamic_fetch=True)
                    for model_meta in models:
                        if model_meta.model == model_name:
                            return model_meta
                except Exception:
                    # Fall back to unknown model metadata
                    pass

            # Unknown model, use fallback metadata
            if provider not in ["openrouter", "local"]:
                log_warn_once(
                    f"Unknown model: using fallback metadata for {provider}/{model_name}"
                )
            return ModelMeta(provider, model_name, context=128_000)
        else:
            # Unknown provider
            logger.warning(f"Unknown model {model}, using fallback metadata")
            return ModelMeta(provider="unknown", model=model, context=128_000)
    else:
        # try to find model in all providers, starting with static models
        for provider in cast(list[Provider], MODELS.keys()):
            if model in MODELS[provider]:
                return ModelMeta(provider, model, **MODELS[provider][model])

        # For model name without provider, also try dynamic fetching for openrouter
        try:
            openrouter_models = _get_models_for_provider(
                "openrouter", dynamic_fetch=True
            )
            for model_meta in openrouter_models:
                if model_meta.model == model:
                    return model_meta
        except Exception:
            pass

        logger.warning(f"Unknown model {model}, using fallback metadata")
        return ModelMeta(provider="unknown", model=model, context=128_000)


def get_recommended_model(provider: Provider) -> str:  # pragma: no cover
    if provider == "openai":
        return "gpt-5"
    elif provider == "openrouter":
        return "meta-llama/llama-3.1-405b-instruct"
    elif provider == "gemini":
        return "gemini-2.5-pro"
    elif provider == "anthropic":
        return "claude-sonnet-4-5"
    else:
        raise ValueError(f"Provider {provider} did not have a recommended model")


def get_summary_model(provider: Provider) -> str:  # pragma: no cover
    if provider == "openai":
        return "gpt-5-mini"
    elif provider == "openrouter":
        return "meta-llama/llama-3.1-8b-instruct"
    elif provider == "gemini":
        return "gemini-2.5-flash"
    elif provider == "anthropic":
        return "claude-haiku-4-5"
    elif provider == "deepseek":
        return "deepseek-chat"
    else:
        raise ValueError(f"Provider {provider} did not have a summary model")


def _get_models_for_provider(
    provider: Provider, dynamic_fetch: bool = True
) -> list[ModelMeta]:
    """Get models for a specific provider, with optional dynamic fetching."""
    from . import get_available_models  # fmt: skip

    models_to_show = []

    # Try dynamic fetching first for supported providers
    if dynamic_fetch and provider == "openrouter":
        try:
            dynamic_models = get_available_models(provider)
            models_to_show = dynamic_models
        except Exception:
            # Fall back to static models
            static_models = [
                get_model(f"{provider}/{name}") for name in MODELS[provider]
            ]
            models_to_show = static_models
    else:
        # Use static models
        if MODELS[provider]:
            static_models = [
                get_model(f"{provider}/{name}") for name in MODELS[provider]
            ]
            models_to_show = static_models

    return models_to_show


def _apply_model_filters(
    models: list[ModelMeta], vision_only: bool = False, reasoning_only: bool = False
) -> list[ModelMeta]:
    """Apply vision and reasoning filters to models."""
    filtered_models = []
    for model in models:
        if vision_only and not model.supports_vision:
            continue
        if reasoning_only and not model.supports_reasoning:
            continue
        filtered_models.append(model)
    return filtered_models


def _print_simple_format(models: list[ModelMeta]) -> None:
    """Print models in simple format (one per line)."""
    for model in models:
        print(f"{model.provider}/{model.model}")


def _format_model_details(model: ModelMeta, show_pricing: bool = False) -> str:
    """Format model details for display."""
    info_parts = [f"  {model.model}"]

    # Context window
    if model.context:
        context_k = model.context // 1000
        info_parts.append(f"{context_k}k ctx")

    # Max output
    if model.max_output:
        output_k = model.max_output // 1000
        info_parts.append(f"{output_k}k out")

    # Vision support
    if model.supports_vision:
        info_parts.append("vision")

    # Reasoning support
    if model.supports_reasoning:
        info_parts.append("reasoning")

    # Pricing
    if show_pricing and (model.price_input or model.price_output):
        price_str = f"${model.price_input:.2f}/${model.price_output:.2f}/1M"
        info_parts.append(price_str)

    return " | ".join(info_parts)


def _print_detailed_format(
    provider: Provider,
    models: list[ModelMeta],
    show_pricing: bool = False,
    dynamic_fetch: bool = True,
) -> None:
    """Print models in detailed format with provider grouping."""
    print(f"\n{provider}:")

    if dynamic_fetch and provider == "openrouter" and len(models) > 0:
        print(f"  ({len(models)} models available via API)")

    # Show up to 10 models with details
    for model in models[:10]:
        print(_format_model_details(model, show_pricing))

    # Show count if more than 10
    if len(models) > 10:
        print(f"  ... ({len(models) - 10} more)")

    # Show empty message if no models configured
    if not models and not MODELS[provider]:
        print("  (no models configured)")


def list_models(
    provider_filter: str | None = None,
    show_pricing: bool = False,
    vision_only: bool = False,
    reasoning_only: bool = False,
    simple_format: bool = False,
    dynamic_fetch: bool = True,
) -> None:
    """
    List available models with optional filtering.

    Args:
        provider_filter: Only show models from this provider
        show_pricing: Include pricing information
        vision_only: Only show models with vision support
        reasoning_only: Only show models with reasoning support
        simple_format: Output one model per line as provider/model
        dynamic_fetch: Fetch dynamic models from APIs where available
    """
    if not simple_format:
        print("Available models:")

    all_models = []

    for provider in MODELS:
        if provider_filter and provider != provider_filter:
            continue

        # Get models for this provider
        models = _get_models_for_provider(provider, dynamic_fetch)

        # Apply filters
        filtered_models = _apply_model_filters(models, vision_only, reasoning_only)

        if not filtered_models:
            continue

        # Output models
        if simple_format:
            all_models.extend(filtered_models)
        else:
            _print_detailed_format(
                provider, filtered_models, show_pricing, dynamic_fetch
            )

    # Print all models in simple format at the end
    if simple_format:
        _print_simple_format(all_models)
