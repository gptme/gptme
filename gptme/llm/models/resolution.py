import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import cast

from .data import MODELS
from .types import (
    _DATE_SUFFIX_PATTERN,
    _MODEL_FAMILY_PATTERN,
    MODEL_ALIASES,
    PROVIDERS,
    ModelMeta,
    Provider,
    _ModelDictMeta,
)

logger = logging.getLogger(__name__)

# default model - using ContextVar for thread safety
_default_model_var: ContextVar[ModelMeta | None] = ContextVar(
    "default_model", default=None
)


def get_default_model() -> ModelMeta | None:
    return _default_model_var.get()


def get_default_model_summary() -> ModelMeta | None:
    """Get the summary model for the default provider.

    Returns the cheaper summary model if available for the provider,
    otherwise returns the default model itself (for local providers, etc.).
    """
    default_model = get_default_model()
    if not default_model:
        return None
    provider = default_model.provider
    assert provider != "unknown"
    summary_model_name = get_summary_model(provider)
    if summary_model_name is None:
        # No summary model defined for this provider (e.g., local)
        # Return the default model instead
        return default_model
    return get_model(f"{provider}/{summary_model_name}")


def set_default_model(model: str | ModelMeta) -> None:
    modelmeta = model if isinstance(model, ModelMeta) else get_model(model)
    assert modelmeta
    _default_model_var.set(modelmeta)


_logged_warnings = set()


def log_warn_once(msg: str):
    if msg not in _logged_warnings:
        logger.warning(msg)
        _logged_warnings.add(msg)


def _get_custom_provider_config(provider_name: str):
    """Get custom provider config by name, returns None if not found."""
    from ...config import get_config  # fmt: skip

    config = get_config()
    for provider in config.user.providers:
        if provider.name == provider_name:
            return provider
    return None


def _find_base_model_properties(
    provider: "Provider", model_name: str
) -> "_ModelDictMeta | None":
    """Find properties from a base model when model_name might be a variant.

    Handles model aliases (e.g., claude-opus-4-1 -> claude-opus-4-1-20250805)
    and date suffixes (e.g., claude-sonnet-4-5-20250929 -> claude-sonnet-4-5).

    Note: This function is called after verifying the exact model name doesn't exist
    in MODELS[provider], so we only need to check for variants and aliases.

    Returns:
        Model properties dict if base model found, None otherwise.
    """
    if provider not in MODELS:
        return None

    provider_models = MODELS[provider]

    # Try alias resolution (e.g., claude-opus-4-1 -> claude-opus-4-1-20250805)
    if provider in MODEL_ALIASES and model_name in MODEL_ALIASES[provider]:
        canonical = MODEL_ALIASES[provider][model_name]
        if canonical in provider_models:
            logger.info(f"Resolved alias {model_name} -> {canonical}")
            return provider_models[canonical]

    # Try stripping date suffix (e.g., -20250929) to find base model
    base_name = _DATE_SUFFIX_PATTERN.sub("", model_name)
    if base_name != model_name and base_name in provider_models:
        logger.debug(f"Using base model properties from {base_name} for {model_name}")
        return provider_models[base_name]

    return None


def _find_closest_model_properties(
    provider: "Provider", model_name: str
) -> "_ModelDictMeta | None":
    """Find properties from the closest known model in the same provider.

    Used as a last resort when exact match, alias, and date-suffix lookups all fail.
    Uses prefix matching to find models in the same family (e.g. claude-sonnet-*),
    preferring the latest non-deprecated model. Falls back to the provider's
    recommended model.

    Returns:
        Model properties dict from the closest match, or None if provider has no models.
    """
    if provider not in MODELS or not MODELS[provider]:
        return None

    provider_models = MODELS[provider]

    # Extract family prefix from the unknown model name
    family_match = _MODEL_FAMILY_PATTERN.match(model_name)
    if family_match:
        family_prefix = family_match.group(1)
        # Find all models in the same family, preferring non-deprecated ones
        candidates: list[tuple[str, _ModelDictMeta]] = []
        for name, props in provider_models.items():
            if name.startswith(family_prefix) and not props.get("deprecated", False):
                candidates.append((name, props))

        if candidates:
            # Pick the candidate with the latest knowledge cutoff, or first if none have cutoffs
            best = max(
                candidates,
                key=lambda c: c[1].get(
                    "knowledge_cutoff", datetime.min.replace(tzinfo=timezone.utc)
                ),
            )
            logger.debug(
                f"Using closest match {best[0]} for unknown model {model_name}"
            )
            return best[1]

    # Fall back to the recommended model's properties for this provider
    try:
        rec_name = get_recommended_model(provider)
        if rec_name in provider_models:
            logger.debug(
                f"Using recommended model {rec_name} as fallback for {model_name}"
            )
            return provider_models[rec_name]
    except ValueError:
        pass

    return None


def get_model(model: str) -> ModelMeta:
    # if only provider is given, get recommended model
    if model in PROVIDERS:
        provider = cast(Provider, model)
        model = get_recommended_model(provider)
        return get_model(f"{provider}/{model}")

    # Check if model is a custom provider name (without model)
    custom_provider = _get_custom_provider_config(model)
    if custom_provider:
        if custom_provider.default_model:
            return get_model(f"{model}/{custom_provider.default_model}")
        raise ValueError(f"Custom provider '{model}' has no default_model configured")

    # Check if model starts with a custom provider prefix
    if "/" in model:
        provider_prefix = model.split("/")[0]
        custom_provider = _get_custom_provider_config(provider_prefix)
        if custom_provider:
            # Custom provider - store full model path, use "unknown" as provider type
            # The routing logic in __init__.py handles custom providers via is_custom_provider()
            return ModelMeta(provider="unknown", model=model, context=128_000)

        # Check if model starts with a plugin provider prefix
        from ..provider_plugins import get_provider_plugin  # fmt: skip

        plugin = get_provider_plugin(provider_prefix)
        if plugin:
            # Look up exact model in plugin's model list
            for m in plugin.models:
                if m.model == model or (
                    m.model.startswith(f"{provider_prefix}/")
                    and m.model.split("/", 1)[-1] == model.split("/", 1)[-1]
                ):
                    return m
            # Model not found in plugin list - return generic metadata so it still works
            log_warn_once(
                f"Model {model!r} not found in plugin {provider_prefix!r} model list; "
                "using generic 128k context fallback"
            )
            return ModelMeta(provider="unknown", model=model, context=128_000)

    # Check if model has provider/model format with built-in provider
    if any(model.startswith(f"{provider}/") for provider in PROVIDERS):
        provider_str, model_name = model.split("/", 1)

        # Check if provider is known
        if provider_str in PROVIDERS:
            provider = cast(Provider, provider_str)

            # For OpenRouter, strip subprovider suffix (e.g., @moonshotai) for static lookup
            # The full model name with suffix is used for API calls, but MODELS dict uses base name
            lookup_model_name = model_name
            if provider == "openrouter" and "@" in model_name:
                lookup_model_name = model_name.split("@")[0]

            # For openai-subscription, strip reasoning level suffix (e.g., :high, :medium)
            # The full model name with suffix is used for API calls, but MODELS dict uses base name
            if provider == "openai-subscription" and ":" in model_name:
                lookup_model_name = model_name.rsplit(":", 1)[0]

            # First try static MODELS dict for performance
            if provider in MODELS and lookup_model_name in MODELS[provider]:
                return ModelMeta(
                    provider, model_name, **MODELS[provider][lookup_model_name]
                )

            # For providers that support dynamic fetching, use _get_models_for_provider
            if provider == "openrouter":
                try:
                    from .listing import _get_models_for_provider  # fmt: skip

                    models = _get_models_for_provider(provider, dynamic_fetch=True)
                    for model_meta in models:
                        # Check both full name (with suffix) and base name (without suffix)
                        if (
                            model_meta.model == model_name
                            or model_meta.model == lookup_model_name
                        ):
                            # Preserve the original model_name (with suffix) in the returned ModelMeta
                            # Use the found model's metadata but with the requested name
                            return ModelMeta(
                                provider=model_meta.provider,
                                model=model_name,  # Preserve original name with suffix
                                context=model_meta.context,
                                max_output=model_meta.max_output,
                                supports_streaming=model_meta.supports_streaming,
                                supports_vision=model_meta.supports_vision,
                                supports_reasoning=model_meta.supports_reasoning,
                                supports_parallel_tool_calls=model_meta.supports_parallel_tool_calls,
                                price_input=model_meta.price_input,
                                price_output=model_meta.price_output,
                                knowledge_cutoff=model_meta.knowledge_cutoff,
                            )
                except Exception as e:
                    # Fall back to unknown model metadata
                    logger.debug(
                        "Failed to fetch OpenRouter models for %s: %s",
                        model_name,
                        e,
                    )

            # Unknown model, try to find base model properties (for variants with date suffixes)
            base_props = _find_base_model_properties(provider, model_name)
            if base_props:
                return ModelMeta(provider, model_name, **base_props)

            # Try closest-match heuristic: find the most similar known model
            closest_props = _find_closest_model_properties(provider, model_name)
            if closest_props:
                if provider not in ("openrouter", "local", "gptme"):
                    log_warn_once(
                        f"Unknown model {provider}/{model_name}: "
                        f"using closest match metadata"
                    )
                return ModelMeta(provider, model_name, **closest_props)

            # No models at all for this provider (e.g. azure, local with no entries)
            if provider not in ("openrouter", "local", "gptme"):
                log_warn_once(
                    f"Unknown model: using generic fallback for {provider}/{model_name}"
                )
            return ModelMeta(provider, model_name, context=128_000)
        # Unknown provider
        logger.warning(f"Unknown model {model}, using fallback metadata")
        return ModelMeta(provider="unknown", model=model, context=128_000)
    # try to find model in all providers, starting with static models
    for provider in cast(list[Provider], MODELS.keys()):
        if model in MODELS[provider]:
            return ModelMeta(provider, model, **MODELS[provider][model])

    # For model name without provider, also try dynamic fetching for openrouter
    try:
        from .listing import _get_models_for_provider  # fmt: skip

        openrouter_models = _get_models_for_provider("openrouter", dynamic_fetch=True)
        # Strip @ suffix for comparison (e.g., "z-ai/glm-5@z-ai" -> "z-ai/glm-5")
        base_model = model.split("@")[0] if "@" in model else model
        for model_meta in openrouter_models:
            if model_meta.model == model or model_meta.model == base_model:
                return ModelMeta(
                    provider=model_meta.provider,
                    model=model,  # Preserve original name with suffix
                    context=model_meta.context,
                    max_output=model_meta.max_output,
                    supports_streaming=model_meta.supports_streaming,
                    supports_vision=model_meta.supports_vision,
                    supports_reasoning=model_meta.supports_reasoning,
                    price_input=model_meta.price_input,
                    price_output=model_meta.price_output,
                    knowledge_cutoff=model_meta.knowledge_cutoff,
                )
    except Exception as e:
        logger.debug("Failed to fetch OpenRouter models for %s: %s", model, e)

    logger.warning(f"Unknown model {model}, using fallback metadata")
    return ModelMeta(provider="unknown", model=model, context=128_000)


def get_recommended_model(provider: Provider) -> str:  # pragma: no cover
    if provider == "openai":
        return "gpt-5"
    if provider == "openai-subscription":
        return "gpt-5.4"
    if provider == "openrouter":
        return "meta-llama/llama-3.1-405b-instruct"
    if provider == "gemini":
        return "gemini-2.5-pro"
    if provider == "anthropic":
        return "claude-sonnet-4-6"
    if provider == "xai":
        return "grok-4"
    if provider == "gptme":
        return "claude-sonnet-4-6"
    if provider == "deepseek":
        return "deepseek-chat"
    if provider == "groq":
        return "llama-3.3-70b-versatile"
    if provider == "litellm":
        raise ValueError(
            "LiteLLM requires an explicit model name, "
            "e.g. gptme -m litellm/anthropic/claude-3-5-sonnet-20241022"
        )
    raise ValueError(
        f"Provider '{provider}' requires specifying a model, "
        f"e.g. gptme -m {provider}/your-model-name"
    )


def get_summary_model(provider: Provider) -> str | None:  # pragma: no cover
    """Get a cheaper/faster summary model for a provider.

    Returns None for providers where no summary model is defined (like local providers),
    signaling that the caller should use the same model.
    """
    if provider == "openai":
        return "gpt-5-mini"
    if provider == "openrouter":
        return "meta-llama/llama-3.1-8b-instruct"
    if provider == "gemini":
        return "gemini-2.5-flash"
    if provider == "anthropic":
        return "claude-haiku-4-5"
    if provider == "deepseek":
        return "deepseek-chat"
    if provider == "xai":
        return "grok-4-1-fast"
    if provider == "local":
        # Local providers don't have predefined summary models
        # Return None to signal "use the same model"
        return None
    # Unknown providers - return None rather than raising
    return None
