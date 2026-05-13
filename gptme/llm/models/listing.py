import json
import logging
import time
from typing import Any, cast

from ..provider_plugins import discover_provider_plugins, get_provider_plugin
from .data import MODELS
from .resolution import get_model
from .types import (
    CustomProvider,
    ModelMeta,
    Provider,
    is_custom_provider,
)

logger = logging.getLogger(__name__)


def model_to_dict(model: ModelMeta) -> dict[str, Any]:
    """Convert a ModelMeta to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "provider": str(model.provider),
        "model": model.model,
        "full": model.full,
        "context": model.context,
    }
    if model.max_output is not None:
        d["max_output"] = model.max_output
    d["supports_streaming"] = model.supports_streaming
    d["supports_vision"] = model.supports_vision
    d["supports_reasoning"] = model.supports_reasoning
    d["supports_responses_api"] = getattr(model, "supports_responses_api", False)
    d["supports_parallel_tool_calls"] = model.supports_parallel_tool_calls
    if model.price_input or model.price_output:
        d["price_input"] = model.price_input
        d["price_output"] = model.price_output
    if model.knowledge_cutoff:
        d["knowledge_cutoff"] = model.knowledge_cutoff.isoformat()
    if model.deprecated:
        d["deprecated"] = True
    if model.preferred_edit_format is not None:
        d["preferred_edit_format"] = model.preferred_edit_format
    return d


def _get_models_for_provider(
    provider: Provider, dynamic_fetch: bool = True
) -> list[ModelMeta]:
    """Get models for a specific provider, with optional dynamic fetching."""
    from .. import get_available_models  # fmt: skip

    # Plugin providers serve their static model list directly
    plugin = get_provider_plugin(str(provider))
    if plugin:
        return list(plugin.models)

    models_to_show = []

    # Try dynamic fetching first for supported providers
    if dynamic_fetch and (
        provider in ("openrouter", "local") or is_custom_provider(provider)
    ):
        try:
            dynamic_models = get_available_models(provider)
            models_to_show = dynamic_models
        except Exception as e:
            # Fall back to static models (only for built-in providers)
            logger.debug(
                "Failed to fetch dynamic models for %s, falling back to static: %s",
                provider,
                e,
            )
            if provider in MODELS:
                static_models = [
                    get_model(f"{provider}/{name}") for name in MODELS[provider]
                ]
                models_to_show = static_models
            # Custom providers have no static fallback
    else:
        # Use static models
        if MODELS.get(provider):
            static_models = [
                get_model(f"{provider}/{name}") for name in MODELS[provider]
            ]
            models_to_show = static_models

    return models_to_show


def _apply_model_filters(
    models: list[ModelMeta],
    vision_only: bool = False,
    reasoning_only: bool = False,
    include_deprecated: bool = False,
) -> list[ModelMeta]:
    """Apply vision, reasoning, and deprecation filters to models."""
    filtered_models = []
    for model in models:
        if not include_deprecated and model.deprecated:
            continue
        if vision_only and not model.supports_vision:
            continue
        if reasoning_only and not model.supports_reasoning:
            continue
        filtered_models.append(model)
    return filtered_models


# Cache for model list (used by completers and CLI)
_model_list_cache: list[ModelMeta] | None = None
_model_list_cache_time: float = 0
_MODEL_LIST_CACHE_TTL = 300  # 5 minutes


def get_model_list(
    provider_filter: str | None = None,
    vision_only: bool = False,
    reasoning_only: bool = False,
    include_deprecated: bool = False,
    dynamic_fetch: bool = True,
) -> list[ModelMeta]:
    """
    Get list of available models with optional filtering.

    This is the underlying function used by list_models() and command completers.
    Results are cached for 5 minutes when dynamic_fetch=True to avoid repeated API calls.

    Args:
        provider_filter: Only include models from this provider
        vision_only: Only include models with vision support
        reasoning_only: Only include models with reasoning support
        include_deprecated: Include deprecated/sunset models (default: False)
        dynamic_fetch: Fetch dynamic models from APIs where available

    Returns:
        List of ModelMeta objects
    """

    from ...config import get_config  # fmt: skip

    global _model_list_cache, _model_list_cache_time

    # Check cache for unfiltered dynamic fetches
    current_time = time.time()
    use_cache = (
        dynamic_fetch
        and not provider_filter
        and not vision_only
        and not reasoning_only
        and not include_deprecated
    )

    if (
        use_cache
        and _model_list_cache is not None
        and current_time - _model_list_cache_time < _MODEL_LIST_CACHE_TTL
    ):
        return _model_list_cache

    all_models: list[ModelMeta] = []

    # Get custom providers from config
    config = get_config()
    custom_providers: list[Provider] = [
        CustomProvider(p.name) for p in config.user.providers
    ]

    # Combine built-in, custom, and plugin providers
    plugin_providers: list[Provider] = [
        CustomProvider(p.name) for p in discover_provider_plugins()
    ]
    all_providers: list[Provider] = (
        list(cast(list[Provider], list(MODELS.keys())))
        + custom_providers
        + plugin_providers
    )

    for provider in all_providers:
        if provider_filter and provider != provider_filter:
            continue

        # Get models for this provider
        models = _get_models_for_provider(provider, dynamic_fetch)

        # Apply filters
        filtered_models = _apply_model_filters(
            models, vision_only, reasoning_only, include_deprecated
        )
        all_models.extend(filtered_models)

    # Update cache for unfiltered results
    if use_cache:
        _model_list_cache = all_models
        _model_list_cache_time = current_time

    return all_models


def _print_simple_format(models: list[ModelMeta]) -> None:
    """Print models in simple format (one per line)."""
    for model in models:
        print(f"{model.provider}/{model.model}")


def _format_model_details(model: ModelMeta, show_pricing: bool = False) -> str:
    """Format model details for display."""
    info_parts = [f"  {model.model}"]

    # Deprecated indicator
    if model.deprecated:
        info_parts.append("DEPRECATED")

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
    is_configured: bool | None = None,
) -> None:
    """Print models in detailed format with provider grouping."""
    if is_configured is not None:
        marker = "\u2713" if is_configured else "\u2717"
        print(f"\n{provider} [{marker}]:")
    else:
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
    if not models and not MODELS.get(provider):
        print("  (no models configured)")


def _get_configured_providers() -> set[str]:
    """Get the set of provider names that have API keys or OAuth configured."""
    from ...llm import list_available_providers  # fmt: skip

    return {provider for provider, _ in list_available_providers()}


def list_models(
    provider_filter: str | None = None,
    show_pricing: bool = False,
    vision_only: bool = False,
    reasoning_only: bool = False,
    include_deprecated: bool = False,
    simple_format: bool = False,
    dynamic_fetch: bool = True,
    available_only: bool = False,
    json_output: bool = False,
) -> None:
    """
    List available models with optional filtering.

    Args:
        provider_filter: Only show models from this provider
        show_pricing: Include pricing information
        vision_only: Only show models with vision support
        reasoning_only: Only show models with reasoning support
        include_deprecated: Include deprecated/sunset models
        simple_format: Output one model per line as provider/model
        dynamic_fetch: Fetch dynamic models from APIs where available
        available_only: Only show models from configured providers
        json_output: Output as JSON
    """
    configured = _get_configured_providers() if available_only else None

    if json_output:
        all_models = get_model_list(
            provider_filter=provider_filter,
            vision_only=vision_only,
            reasoning_only=reasoning_only,
            include_deprecated=include_deprecated,
            dynamic_fetch=dynamic_fetch,
        )
        if configured is not None:
            all_models = [m for m in all_models if m.provider_key in configured]
        print(json.dumps([model_to_dict(m) for m in all_models], indent=2))
    elif simple_format:
        # Simple format: just get all models and print them
        all_models = get_model_list(
            provider_filter=provider_filter,
            vision_only=vision_only,
            reasoning_only=reasoning_only,
            include_deprecated=include_deprecated,
            dynamic_fetch=dynamic_fetch,
        )
        if configured is not None:
            all_models = [m for m in all_models if m.provider_key in configured]
        _print_simple_format(all_models)
    else:
        # Detailed format: print by provider with formatting
        from ...config import get_config  # fmt: skip

        configured_set = (
            configured if configured is not None else _get_configured_providers()
        )
        if available_only:
            print("Models from configured providers:")
        else:
            print("Available models:")

        config = get_config()
        custom_providers: list[Provider] = [
            CustomProvider(p.name) for p in config.user.providers
        ]
        plugin_providers_detail: list[Provider] = [
            CustomProvider(p.name) for p in discover_provider_plugins()
        ]
        all_providers: list[Provider] = (
            list(cast(list[Provider], list(MODELS.keys())))
            + custom_providers
            + plugin_providers_detail
        )

        for provider in all_providers:
            if provider_filter and provider != provider_filter:
                continue

            if available_only and provider not in configured_set:
                continue

            models = _get_models_for_provider(provider, dynamic_fetch)
            filtered_models = _apply_model_filters(
                models, vision_only, reasoning_only, include_deprecated
            )

            if not filtered_models:
                continue

            is_configured = provider in configured_set
            _print_detailed_format(
                provider,
                filtered_models,
                show_pricing,
                dynamic_fetch,
                is_configured=is_configured,
            )
