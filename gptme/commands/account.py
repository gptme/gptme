"""Account management command for provider onboarding and status."""

import logging
from typing import cast

from rich.prompt import Prompt

from ..config import set_config_value
from ..credentials import (
    STORED_CREDENTIALS_SOURCE,
    get_stored_api_key,
    mask_secret,
    set_stored_api_key,
)
from ..llm import PROVIDER_DEFAULT_MODELS, list_available_providers
from ..llm.validate import validate_api_key
from .base import CommandContext, command

logger = logging.getLogger(__name__)

MANUAL_SETUP_PROVIDERS = ("anthropic", "openai", "deepseek", "gemini", "groq", "xai")
SETUP_PROVIDER_HELP = {
    "openrouter": "Browser sign-in (OAuth / PKCE)",
    "anthropic": "Paste API key",
    "openai": "Paste API key",
    "deepseek": "Paste API key",
    "gemini": "Paste API key",
    "groq": "Paste API key",
    "xai": "Paste API key",
}


def _get_available_providers() -> list[tuple[str, str]]:
    """List configured providers with their auth source."""
    return sorted(
        (str(provider), source) for provider, source in list_available_providers()
    )


def _get_active_provider() -> str | None:
    """Detect the currently active provider."""
    from ..llm.models import get_default_model

    model = get_default_model()
    if model:
        return model.provider
    return None


def _switch_anthropic(api_key: str) -> None:
    """Reinit Anthropic client with new key."""
    from ..llm.llm_anthropic import reinit as reinit_anthropic

    reinit_anthropic(api_key)
    logger.debug("Anthropic client re-initialized")


def _switch_openai_like(provider: str, api_key: str) -> None:
    """Reinit OpenAI-compatible client with new key."""
    from ..llm.llm_openai import reinit as reinit_openai
    from ..llm.models.types import Provider

    reinit_openai(cast(Provider, provider), api_key=api_key)
    logger.debug("OpenAI client re-initialized for %s", provider)


def _refresh_runtime_provider(provider: str, api_key: str) -> None:
    """Update the in-memory client if the provider is already initialized."""
    try:
        if provider == "anthropic":
            _switch_anthropic(api_key)
        elif provider in ("openai", "openrouter", "deepseek", "gemini", "groq", "xai"):
            _switch_openai_like(provider, api_key)
    except ValueError:
        logger.debug(
            "Provider %s is not initialized in this session; skipping refresh", provider
        )


def _get_key_preview(provider: str, source: str) -> str:
    """Render a safe preview for the configured auth source."""
    from ..config import get_config

    if source == STORED_CREDENTIALS_SOURCE:
        api_key = get_stored_api_key(provider)
        return mask_secret(api_key) if api_key else "-"
    if source == "oauth":
        return "token on disk"
    api_key = get_config().get_env(source)
    return mask_secret(api_key) if api_key else "-"


def _select_setup_provider() -> str:
    """Interactively choose a provider for `/account setup`."""
    provider_items = list(SETUP_PROVIDER_HELP.items())
    print("Choose a provider to set up:")
    for idx, (provider, help_text) in enumerate(provider_items, start=1):
        print(f"  {idx}. {provider:10s} {help_text}")

    while True:
        choice = Prompt.ask("Provider", default="1").strip().lower()
        try:
            index = int(choice) - 1
        except ValueError:
            index = -1
        if 0 <= index < len(provider_items):
            return provider_items[index][0]
        if choice in SETUP_PROVIDER_HELP:
            return choice
        print(f"Unknown provider: {choice}")


def _set_default_model(provider: str) -> None:
    """Switch the default model to the provider's recommended default."""
    model = PROVIDER_DEFAULT_MODELS.get(provider)
    if not model:
        return
    set_config_value("env.MODEL", model)
    print(f"Default model set to {model}.")


def _setup_openrouter() -> None:
    """Run the OpenRouter OAuth flow and persist the resulting key."""
    from ..oauth.openrouter import OAuthError, authenticate

    print("Starting OpenRouter browser sign-in...")
    try:
        api_key = authenticate()
    except OAuthError as e:
        print(f"OpenRouter OAuth failed: {e}")
        return

    path = set_stored_api_key("openrouter", api_key)
    _refresh_runtime_provider("openrouter", api_key)
    _set_default_model("openrouter")
    print(f"Stored OpenRouter key in {path}.")


def _setup_manual_provider(provider: str) -> None:
    """Prompt for a provider API key, validate it, and store it."""
    api_key = Prompt.ask(f"{provider} API key", password=True).strip()
    if not api_key:
        print("No API key provided.")
        return

    is_valid, message = validate_api_key(api_key, provider)
    if not is_valid:
        print(f"{provider} API key validation failed: {message}")
        return

    path = set_stored_api_key(provider, api_key)
    _refresh_runtime_provider(provider, api_key)
    _set_default_model(provider)
    print(f"Stored {provider} key in {path}.")
    if message:
        print(message)


def _list_accounts() -> None:
    """Print configured provider credentials."""
    available = _get_available_providers()
    active = _get_active_provider()

    if not available:
        print("No configured provider credentials found.")
        print("Run /account setup to add one.")
        return

    print(f"Configured accounts (active: {active or 'none'}):")
    print()
    for provider, source in available:
        marker = " ◀ (active)" if provider == active else ""
        preview = _get_key_preview(provider, source)
        print(f"  {provider:18s} {source:16s} {preview}{marker}")


@command("account", aliases=["creds"])
def cmd_account(ctx: CommandContext):
    """Show account status and run provider onboarding.

    Usage:
        /account
        /account list
        /account setup
        /account setup openrouter
        /account setup anthropic
    """
    if not ctx.args or ctx.args[0] == "list":
        _list_accounts()
        return

    subcommand = ctx.args[0]

    if subcommand == "setup":
        provider = (
            ctx.args[1].lower() if len(ctx.args) >= 2 else _select_setup_provider()
        )
        if provider == "openrouter":
            _setup_openrouter()
            return
        if provider in MANUAL_SETUP_PROVIDERS:
            if len(ctx.args) >= 3:
                print(
                    "Passing API keys on the command line is not supported. "
                    f"Re-run `/account setup {provider}` and paste the key into the hidden prompt."
                )
                return
            _setup_manual_provider(provider)
            return

        print(f"Unknown provider: {provider}")
        print("Supported: openrouter, anthropic, openai, deepseek, gemini, groq, xai")
        return

    print(f"Unknown subcommand: {subcommand}")
    print("Usage: /account [list|setup [provider]]")
