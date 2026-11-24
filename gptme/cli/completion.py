"""Shared Click parameter completions. Keep imports lazy for CLI startup."""

import click
from click.shell_completion import CompletionItem


def complete_model(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete provider/model names offline, without authenticating or querying APIs.

    Use bundled model metadata plus configured custom-provider defaults. Shell
    completion runs in a fresh process per keystroke, so an in-memory API cache
    cannot prevent repeated requests (or authentication prompts).
    """
    import tomlkit

    from ..config.user import (
        _merge_config_data,
        _parse_providers,
        get_user_config_paths,
        get_user_config_runtime_path,
    )
    from ..llm.models import MODELS, PROVIDERS

    names: set[str] = set(PROVIDERS)
    names.update(
        f"{provider}/{model}" for provider, models in MODELS.items() for model in models
    )
    # The normal config loader initializes missing files and strips unknown
    # keys. Tab must be read-only, and a broken config must not hide bundled
    # suggestions. Preserve runtime < main < local provider precedence.
    providers: dict = {}
    for path in (get_user_config_runtime_path(), *get_user_config_paths()):
        try:
            data = tomlkit.loads(path.read_text(encoding="utf-8")).unwrap()
        except (OSError, UnicodeError, tomlkit.exceptions.TOMLKitError):
            continue
        providers = _merge_config_data(
            providers, {"providers": data.get("providers", [])}
        )
    for provider in _parse_providers(providers.get("providers", []), strict=False):
        if provider.default_model:
            names.add(provider.name)
            names.add(f"{provider.name}/{provider.default_model}")
    return [
        CompletionItem(name) for name in sorted(names) if name.startswith(incomplete)
    ]
