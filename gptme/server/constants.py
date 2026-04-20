"""Server-wide constants and configuration defaults."""

from gptme.llm import PROVIDER_DEFAULT_MODELS

# Default model to use when no model is configured
# This is used as a fallback in cli.py and shown as an example in api_v2_sessions.py
DEFAULT_FALLBACK_MODEL = "anthropic/claude-sonnet-4-6"

# Per-provider fallback models: imported from gptme.llm to avoid duplication with
# the equivalent dict in gptme.cli.util. The goal is "server starts for first-run UX"
# not "optimal model choice" — users set MODEL explicitly for real use.
PROVIDER_FALLBACK_MODELS = PROVIDER_DEFAULT_MODELS


def _pick_fallback_model() -> str:
    """Pick a fallback model based on which providers are actually configured.

    Returns a model string for the first available provider found in
    PROVIDER_FALLBACK_MODELS, falling back to DEFAULT_FALLBACK_MODEL if nothing
    is configured (so the caller still sees a helpful init() error).
    """
    from gptme.llm import list_available_providers

    for provider, _auth in list_available_providers():
        if provider in PROVIDER_FALLBACK_MODELS:
            return PROVIDER_FALLBACK_MODELS[provider]
    return DEFAULT_FALLBACK_MODEL
