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
    from ..config import get_config
    from ..llm.models import MODELS, PROVIDERS

    names = {f"{provider}/" for provider in PROVIDERS}
    names.update(
        f"{provider}/{model}" for provider, models in MODELS.items() for model in models
    )
    for provider in get_config().user.providers:
        names.add(f"{provider.name}/")
        if provider.default_model:
            names.add(f"{provider.name}/{provider.default_model}")
    return [
        CompletionItem(name) for name in sorted(names) if name.startswith(incomplete)
    ]
