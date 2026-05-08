"""
Account management command: list and switch credentials per provider.

Usage:
    /account              List configured provider credentials
    /account switch <provider> <key>  Temporarily switch to a new API key for the current session

Implements the session-level credential switching from gptme#2313.
"""

import logging
import os
from pathlib import Path
from typing import cast

from ..config import get_config
from .base import CommandContext, command

logger = logging.getLogger(__name__)


def _get_available_providers() -> list[tuple[str, str]]:
    """List configured providers with their auth source.

    Returns list of (provider_name, auth_source) tuples.
    """
    from ..llm import PROVIDER_API_KEYS, get_plugin_api_keys

    config = get_config()
    available: list[tuple[str, str]] = []

    for provider, env_var in PROVIDER_API_KEYS.items():
        if config.get_env(env_var):
            available.append((provider, env_var))

    # Plugin providers
    for plugin_name, env_var in get_plugin_api_keys().items():
        if config.get_env(env_var):
            available.append((plugin_name, env_var))

    # OAuth-based providers
    config_dir = Path(
        os.environ.get(
            "XDG_CONFIG_HOME",
            Path.home() / ".config",
        )
    )
    token_path = config_dir / "gptme" / "oauth" / "openai_subscription.json"
    if "openai-subscription" not in {p for p, _ in available} and token_path.exists():
        available.append(("openai-subscription", "oauth"))

    return sorted(available)


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


def _list_accounts() -> None:
    """Print configured provider credentials."""
    available = _get_available_providers()
    active = _get_active_provider()

    if not available:
        print("No configured provider credentials found.")
        print("Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, OPENROUTER_API_KEY, etc.")
        return

    print(f"Available accounts (active: {active or 'none'}):")
    print()
    for provider, source in available:
        marker = " ◀ (active)" if provider == active else ""
        print(f"  {provider:25s} {source}{marker}")


@command("account", aliases=["creds"])
def cmd_account(ctx: CommandContext):
    """Show or switch provider credentials.

    Usage:
        /account                  List configured accounts
        /account switch <provider> <key>   Switch to a new API key for the session

    The switch is session-level only and does not persist to config files.
    """
    if not ctx.args:
        _list_accounts()
        return

    subcommand = ctx.args[0]

    if subcommand == "switch":
        if len(ctx.args) < 3:
            print("Usage: /account switch <provider> <api_key>")
            print("Example: /account switch anthropic sk-ant-xxxx")
            return

        provider = ctx.args[1]
        api_key = ctx.args[2]

        try:
            if provider == "anthropic":
                _switch_anthropic(api_key)
            elif provider in (
                "openai",
                "openrouter",
                "deepseek",
                "gemini",
                "groq",
                "xai",
            ):
                _switch_openai_like(provider, api_key)
            else:
                print(f"Unknown provider: {provider}")
                print(
                    "Supported: anthropic, openai, openrouter, deepseek, gemini, groq, xai"
                )
                return
        except ValueError as e:
            print(f"Error switching {provider} credentials: {e}")
            return

        print(f"Switched {provider} credentials.")
        print("New credentials active for subsequent messages.")
        return

    print(f"Unknown subcommand: {subcommand}")
    print("Usage: /account [switch <provider> <key>]")
