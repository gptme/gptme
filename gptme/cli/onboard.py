"""
First-run onboarding wizard for gptme.

Usage:
    gptme-onboard              # Run the setup wizard
    gptme-onboard --check      # Check current setup status
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import cast

import click
import tomlkit
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from ..config import config_path, get_config, set_config_value
from ..config.user import (
    USER_CONFIG_SOURCE_LOCAL,
    _config_write_lock,
    _initial_config_data,
    _load_config_doc,
    _write_config_text,
    get_default_model_source,
    get_user_config_paths,
)
from ..credentials import STORED_CREDENTIALS_SOURCE, get_stored_api_key
from ..llm import PROVIDER_API_KEYS, list_available_providers
from ..llm.models import MODELS, PROVIDERS, BuiltinProvider, get_recommended_model
from ..llm.validate import (
    OAUTH_PROVIDERS,
    PROVIDER_DOCS,
    ApiKeyValidationStatus,
    validate_api_key_status,
)

logger = logging.getLogger(__name__)
console = Console()

# Environment variable patterns for each provider (used by detection and testing)
PROVIDER_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


def _detect_providers() -> dict[str, tuple[bool, str | None]]:
    """
    Detect which providers have API keys configured (in env or config file).

    Checks environment variables first, then falls back to the config file
    for API keys stored in the ``[env]`` section or a model set in ``[chat]``.

    Returns:
        Dict mapping provider name to (has_key, key_preview)
    """
    results: dict[str, tuple[bool, str | None]] = {}

    # Check environment variables (highest priority, shows key preview)
    for provider, env_var in PROVIDER_ENV_VARS.items():
        key = os.environ.get(env_var)
        if key:
            # Preview: show first 4 and last 4 chars
            preview = f"{key[:4]}...{key[-4:]}" if len(key) > 10 else "***"
            results[provider] = (True, preview)
        else:
            results[provider] = (False, None)

    # OAuth providers have no API key; use the same token-file detection as the
    # runtime and ``gptme-doctor``. Credential/config errors must not discard
    # providers already detected from the environment.
    try:
        for provider, source in list_available_providers():
            if provider in OAUTH_PROVIDERS:
                results[provider] = (True, source)
            elif (
                source == STORED_CREDENTIALS_SOURCE
                and not results.get(provider, (False,))[0]
            ):
                # Keys saved by `/account setup` live in credentials.toml.
                results[provider] = (True, f"({STORED_CREDENTIALS_SOURCE})")
    except (OSError, ValueError) as e:
        logger.warning(
            "OAuth credential check failed: %s — run `gptme auth` to re-authenticate", e
        )

    # Also check config file for API keys and configured model
    try:
        from ..config import get_config

        config = get_config()

        # Check config [env] section for API keys not found in environment
        for provider, env_var in PROVIDER_ENV_VARS.items():
            if not results.get(provider, (False,))[0]:
                config_key = config.get_env(env_var)
                if config_key:
                    preview = (
                        f"{config_key[:4]}...{config_key[-4:]}"
                        if len(config_key) > 10
                        else "***"
                    )
                    results[provider] = (True, f"{preview} (config)")

        # Check if a model is already configured (implies provider is available)
        model = (config.chat.model if config.chat else None) or config.get_env("MODEL")
        if model and "/" in model:
            provider_name = model.split("/")[0]
            if provider_name in results and not results[provider_name][0]:
                results[provider_name] = (True, f"(model: {model})")
    except Exception:
        pass  # Config not available yet — env var detection is sufficient

    return results


def _test_provider(provider: str) -> tuple[bool, str]:
    """Test if a provider is working (checks env vars and config file)."""
    if provider in OAUTH_PROVIDERS:
        try:
            available = {name for name, _ in list_available_providers()}
        except (OSError, ValueError) as e:
            logger.warning(
                "OAuth credential check failed: %s — run `gptme auth %s` to re-authenticate",
                e,
                provider,
            )
            return False, f"Could not read OAuth credentials: {e}"
        if provider in available:
            return True, "OAuth credentials found (not validated during onboarding)"
        return False, f"Not authenticated (run gptme auth {provider})"

    # Detection also lists any provider with a key in credentials.toml (e.g.
    # moonshot), so look that key up before rejecting a provider as unknown.
    env_var = PROVIDER_ENV_VARS.get(provider) or PROVIDER_API_KEYS.get(provider)
    api_key: str | None = None

    # Check env var first, then config file
    if env_var:
        api_key = os.environ.get(env_var)
    if env_var and not api_key:
        try:
            from ..config import get_config

            api_key = get_config().get_env(env_var)
        except Exception:
            pass
    if not api_key:
        try:
            api_key = get_stored_api_key(provider)
        except (OSError, ValueError):
            api_key = None
    if not api_key:
        if not env_var:
            return False, f"Unknown provider: {provider}"
        return False, f"No API key found (set {env_var})"

    status, message = validate_api_key_status(api_key, provider)
    if status == ApiKeyValidationStatus.UNSUPPORTED:
        return True, message or "API key found (not validated during onboarding)"
    return status != ApiKeyValidationStatus.INVALID, message


def _show_provider_status(providers: dict[str, tuple[bool, str | None]]) -> None:
    """Display provider status in a nice table."""
    table = Table(title="API Provider Status")
    table.add_column("Provider", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Key Preview")
    table.add_column("Docs")

    # Show all builtin providers plus any additional (e.g. OAuth) providers
    # detected in `providers` that aren't in the builtin list, so a
    # configured OAuth provider isn't silently omitted from the table.
    all_providers = list(PROVIDERS) + [p for p in providers if p not in PROVIDERS]
    for provider in all_providers:
        has_key, preview = providers.get(provider, (False, None))
        if has_key:
            status = "✅ Configured"
            preview_text = preview or "***"
        else:
            status = "❌ Not configured"
            preview_text = "-"

        docs_url = PROVIDER_DOCS.get(provider, "")
        table.add_row(provider, status, preview_text, docs_url)

    console.print(table)


def _get_default_model(provider: str) -> str:
    """Return a valid default model string for the given provider.

    For providers with a builtin recommended model, returns ``provider/model``.
    For OAuth/subscription providers that have no builtin recommendation, falls
    back to the first model listed in the static ``MODELS`` dict so the default
    is always a fully-qualified ``provider/model`` string rather than the bare
    provider name (which fails at startup).

    Returns an empty string if no default model can be determined.
    Callers must treat an empty return as "no default available" and require
    the user to supply a full ``provider/model`` string explicitly.
    """
    if provider in PROVIDERS:
        try:
            rec = get_recommended_model(cast(BuiltinProvider, provider))
            return f"{provider}/{rec}"
        except ValueError:
            pass
    # Fallback: first model in the static MODELS dict, if any.
    # MODELS keys are Literal types; cast provider str to avoid the mypy overload mismatch.
    provider_models = list(MODELS.get(cast(BuiltinProvider, provider), {}).keys())
    if provider_models:
        return f"{provider}/{provider_models[0]}"
    # No default known — returning the bare provider name would produce an invalid
    # config (runtime requires provider/model).  Let the caller ask the user.
    return ""


def _select_provider(providers: dict[str, tuple[bool, str | None]]) -> str | None:
    """Let user select a provider from available ones."""
    available = [p for p, (has_key, _) in providers.items() if has_key]

    if not available:
        return None

    # Show selection prompt
    console.print("\n[bold]Available providers:[/bold]")
    for i, provider in enumerate(available, 1):
        # Cast to BuiltinProvider for type safety (we only support builtins here)
        if provider in PROVIDERS:
            try:
                rec_model = get_recommended_model(cast(BuiltinProvider, provider))
                console.print(f"  {i}. {provider} (recommended: {rec_model})")
            except ValueError:
                # Provider doesn't have a recommended model configured
                console.print(f"  {i}. {provider}")
        else:
            console.print(f"  {i}. {provider}")

    default_choice = "1" if available else ""
    while True:
        choice = Prompt.ask(
            "\nSelect provider (number or name)", default=default_choice
        )

        if not choice:
            return None

        # Try as number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(available):
                return available[idx]
        except ValueError:
            pass

        # Try as name
        if choice.lower() in available:
            return choice.lower()

        console.print("[red]Invalid selection. Try again.[/red]")


def _get_corrupt_config_paths() -> list[Path]:
    """Identify unreadable user configs before asking permission to move them."""
    corrupt_paths = []
    for path in get_user_config_paths():
        if not path.exists():
            continue
        try:
            _load_config_doc(str(path))
        except tomlkit.exceptions.TOMLKitError:
            corrupt_paths.append(path)
    return corrupt_paths


def _back_up_corrupt_configs(paths: list[Path]) -> None:
    """Move unparseable user config files aside so onboarding can rewrite them.

    Onboarding used to offer to overwrite an existing config. Now that it saves
    through the shared writer, a corrupt file must be removed first or the
    writer (and ``get_config``) would keep failing on it.

    Each move resolves the path first: a config symlinked from a dotfiles
    checkout must be replaced at its target, not have the link itself moved.
    Backups get a unique suffix so a second incident cannot erase the copy
    saved by the first.
    """
    for path in paths:
        # Each file has its own sidecar lock. In particular, locking only the
        # main config would not exclude a concurrent config.local.toml save.
        with _config_write_lock(path):
            if not path.exists():
                continue
            target = path.resolve()
            try:
                _load_config_doc(str(target))
            except tomlkit.exceptions.TOMLKitError:
                backup = target.with_name(
                    f"{target.name}.corrupt-{uuid.uuid4().hex[:8]}"
                )
                target.replace(backup)
                # A symlinked config must keep resolving: recreate the file at
                # the link's target, or the wizard would skip the dangling link
                # and write the other config, silently losing the local override.
                # Give it the content a missing config would be created with: an
                # empty file parses as an empty document, so the rewrite would
                # keep only [models] where a plain corrupt file gets the full
                # defaults. The shared writer also keeps config.local.toml 0600.
                if path.is_symlink():
                    _write_config_text(
                        path,
                        tomlkit.dumps(_initial_config_data(str(path))),
                        exclusive=True,
                    )
                console.print(f"[yellow]Moved corrupt config to: {backup}[/yellow]")


def _config_sets_non_table_models(path: Path) -> bool:
    """True when a user config sets ``models`` to something but a table.

    In ``config.local.toml`` a scalar or array ``models`` key replaces the whole
    ``[models]`` table on merge, so a default saved to ``config.toml`` is
    discarded on the next load while onboarding still reports success. In
    ``config.toml`` itself the writer cannot add ``models.default`` under it.
    """
    if not path.exists():
        return False
    try:
        models = _load_config_doc(str(path)).unwrap().get("models")
    except tomlkit.exceptions.TOMLKitError:
        return False
    return models is not None and not isinstance(models, dict)


def _refuse_non_table_models(path: Path, shadows_main: bool) -> None:
    console.print(f"\n[red]{path} sets `models` to a value that is not a table.[/red]")
    if shadows_main:
        reason = (
            "It replaces the whole [models] table from config.toml, so a default "
            "saved there would be discarded."
        )
    else:
        reason = "A default model cannot be saved under it."
    console.print(
        f"{reason} Fix that entry to a [models] table and re-run onboarding.",
        markup=False,
    )


def _current_default_model() -> tuple[str | None, bool]:
    """Return the user config's effective default model and whether it is local.

    ``[models].default`` outranks the user config's ``[env].MODEL``, so a
    default saved by onboarding would silently shadow that variable. Count it
    as the current default so the replacement is confirmed first.
    """
    user = get_config().user
    current = user.models.default or user.env.get("MODEL") or None
    in_local = get_default_model_source() == USER_CONFIG_SOURCE_LOCAL
    return current, in_local


def _create_config(provider: str, model: str | None = None) -> Path | None:
    """Save the chosen default model to the gptme config file.

    Writes ``[models] default`` (the key gptme reads) through the shared config
    writer, so the rest of an existing config is kept as-is. Returns None if
    the user declines a change, rather than reporting a successful save.
    """
    conf_path = Path(config_path)

    if model is None:
        if provider in PROVIDERS:
            try:
                rec_model = get_recommended_model(cast(BuiltinProvider, provider))
                model = f"{provider}/{rec_model}"
            except ValueError:
                # Provider doesn't have a recommended model configured
                model = provider
        else:
            model = provider

    try:
        current, in_local = _current_default_model()
    except tomlkit.exceptions.TOMLKitError:
        corrupt_paths = _get_corrupt_config_paths()
        if not corrupt_paths:
            raise
        console.print("\n[red]These user config files are not valid TOML:[/red]")
        for path in corrupt_paths:
            console.print(f"  {path}", markup=False)
        console.print(
            "[yellow]Recovery will move these files to backups. "
            "Settings such as API keys must be restored from those backups.[/yellow]"
        )
        if not Confirm.ask("Back up these files and continue?", default=True):
            console.print("[dim]Skipping config creation.[/dim]")
            return None
        _back_up_corrupt_configs(corrupt_paths)
        # Re-read what survived the backup. A valid config.local.toml may still
        # set a default, and that must go through the same replacement
        # confirmation as the non-corrupt path: the user only agreed to move
        # the unparseable file aside, not to replace a default that was never
        # corrupt.
        try:
            current, in_local = _current_default_model()
        except tomlkit.exceptions.TOMLKitError:
            current = None
            in_local = False
        except ValueError as e:
            # A surviving config parses but is not a valid user config (e.g.
            # ``hooks`` set to a scalar). The corrupt files are already in
            # their backups; say what is left instead of a traceback.
            console.print(f"\n[red]The user config is still invalid: {e}[/red]")
            console.print(
                "Fix that setting and re-run onboarding. "
                "The moved files are kept in the backups listed above.",
                markup=False,
            )
            return None

    # config.local.toml overrides config.toml, so update it if it sets one.
    # Use the startup loader, which also accepts legacy-encoded files.
    main_path, local_path = get_user_config_paths()
    # The local file is also the write target when it sets the default (or
    # ``[env].MODEL``), so its own non-table ``models`` must be refused on that
    # path too: the writer would otherwise raise after the user confirmed the
    # replacement.
    if _config_sets_non_table_models(local_path):
        _refuse_non_table_models(local_path, shadows_main=True)
        return None
    if not in_local and _config_sets_non_table_models(main_path):
        _refuse_non_table_models(main_path, shadows_main=False)
        return None

    if current and current != model:
        console.print(f"\n[yellow]Default model already set: {current}[/yellow]")
        if not Confirm.ask(f"Replace it with {model}?"):
            console.print("[dim]Keeping the existing default model.[/dim]")
            return None

    if in_local:
        conf_path = local_path
    set_config_value("models.default", model, local=in_local)
    console.print(f"\n[green]✅ Default model saved to: {conf_path}[/green]")

    return conf_path


def _run_wizard(check_only: bool = False) -> int:
    """Run the onboarding wizard."""
    console.print(
        Panel.fit(
            "[bold blue]gptme Onboarding Wizard[/bold blue]\n\n"
            "This wizard will help you set up gptme with your preferred AI provider.",
            title="Welcome",
            border_style="blue",
        )
    )

    # Step 1: Detect providers
    console.print("\n[bold]Step 1: Detecting API providers...[/bold]")
    providers = _detect_providers()
    _show_provider_status(providers)

    available_providers = [p for p, (has_key, _) in providers.items() if has_key]

    if not available_providers:
        console.print("\n[red]❌ No provider configured![/red]")
        console.print("\nTo use gptme, sign in with a subscription or set an API key.")
        console.print(
            "\n[bold]Option 1[/bold] — sign in with a subscription you already pay for:"
        )
        console.print(
            "  [cyan]gptme-auth openai-subscription[/cyan]    # ChatGPT Plus/Pro"
        )
        console.print("  [cyan]gptme-auth grok-subscription[/cyan]      # SuperGrok")
        console.print("\n[bold]Option 2[/bold] — set an API key:")
        console.print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        console.print("  export OPENAI_API_KEY='sk-...'")
        console.print("  export OPENROUTER_API_KEY='sk-or-...'")
        console.print(
            "\nThen re-run [bold]gptme-onboard[/bold] or just start with [bold]gptme[/bold]."
        )
        console.print("\nFull guide: https://gptme.org/docs/getting-started.html")
        return 1

    if check_only:
        console.print(
            f"\n[green]✅ {len(available_providers)} provider(s) configured[/green]"
        )
        return 0

    # Step 2: Select provider
    console.print("\n[bold]Step 2: Select default provider[/bold]")
    selected = _select_provider(providers)

    if not selected:
        console.print("[red]No provider selected.[/red]")
        return 1

    console.print(f"\n[green]Selected: {selected}[/green]")

    # Step 3: Test connectivity
    console.print(f"\n[bold]Step 3: Testing {selected} connectivity...[/bold]")
    is_valid, error = _test_provider(selected)

    if is_valid:
        if error:
            console.print(f"[yellow]⚠️ {error}[/yellow]")
        else:
            console.print(f"[green]✅ Successfully connected to {selected}![/green]")
    else:
        console.print(f"[yellow]⚠️ Connection test failed: {error}[/yellow]")
        if not Confirm.ask("Continue anyway?"):
            return 1

    # Step 4: Create config
    console.print("\n[bold]Step 4: Create configuration[/bold]")

    # Ask for model preference
    default_model = _get_default_model(selected)
    if selected in OAUTH_PROVIDERS:
        # Show a hint for OAuth providers so the user knows the required format.
        example = f" (e.g. {default_model})" if default_model else ""
        console.print(
            f"[dim]Note: {selected} requires specifying a model.  "
            f"Enter it as [bold]{selected}/MODEL-NAME[/bold]{example}.[/dim]"
        )
    while True:
        if default_model:
            model = Prompt.ask("Default model", default=default_model)
        else:
            model = Prompt.ask("Default model (e.g. provider/model-name)")
        provider, separator, model_name = model.partition("/")
        if (
            separator
            and provider == selected
            and model_name
            and not model_name.endswith("/")
        ):
            break
        console.print(
            f"[red]Model must start with {selected}/ followed by a model name (e.g. {selected}/MODEL-NAME).[/red]"
        )

    # Create config
    config_created = _create_config(selected, model)
    if config_created is None:
        console.print(
            "[yellow]Configuration was not changed; setup cancelled.[/yellow]"
        )
        return 1

    # Final summary
    console.print(
        Panel.fit(
            f"[green]✅ Setup complete![/green]\n\n"
            f"Provider: {selected}\n"
            f"Model: {model}\n"
            f"Config: {config_created}\n\n"
            f"Run [bold]gptme[/bold] to start chatting!",
            title="🎉 Ready to go!",
            border_style="green",
        )
    )

    return 0


@click.command()
@click.option("--check", is_flag=True, help="Check setup status without making changes")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
def main(check: bool = False, verbose: bool = False) -> None:
    """
    First-run onboarding wizard for gptme.

    Helps you:
    - Detect available API providers
    - Select your preferred model
    - Create initial configuration
    - Test connectivity
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    try:
        sys.exit(_run_wizard(check_only=check))
    except KeyboardInterrupt:
        console.print("\n[dim]Setup cancelled.[/dim]")
        sys.exit(130)


if __name__ == "__main__":
    main()
