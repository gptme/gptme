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
        entries = data.get("providers", [])
        if not isinstance(entries, list):
            continue
        entries = [
            entry
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get("name"), str)
        ]
        providers = _merge_config_data(providers, {"providers": entries})
    for provider in _parse_providers(providers.get("providers", []), strict=False):
        if provider.default_model:
            names.add(provider.name)
            names.add(f"{provider.name}/{provider.default_model}")
    return [
        CompletionItem(name) for name in sorted(names) if name.startswith(incomplete)
    ]


def complete_tools(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Discover built-in tool choices without loading or initializing user config."""
    from ..config.core import Config, _config_var
    from ..config.user import default_config

    # Tool discovery consults get_config(), even with MCP disabled. Scope an
    # in-memory config to this callback so Tab cannot write files or load user
    # plugins. Restore it for callers using Click completion in-process.
    token = _config_var.set(Config(user=default_config))
    try:
        return param.type.shell_complete(ctx, param, incomplete)
    finally:
        _config_var.reset(token)


def complete_prompt(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Mirror ``gptme <util-subcommand> ...`` dispatch; otherwise complete paths.

    ``gptme chats list`` runs ``gptme-util chats list``, so its words complete
    from gptme-util's command tree. Other prompt words are free text or file
    paths given as context.
    """
    from click.shell_completion import ShellComplete

    from .util import UTIL_SUBCOMMANDS

    prompts = list(ctx.params.get("prompts") or ())
    if not ctx.meta.get("shortcut_dispatch_suppressed"):
        if not prompts:
            names = [
                n for n in ["search", *UTIL_SUBCOMMANDS] if n.startswith(incomplete)
            ]
            if names and incomplete:
                # The first word may also be a context file (`gptme main.py`),
                # so offer paths too. File comes first: Click's Bash script
                # resets COMPREPLY on a file item, so Bash keeps the names.
                return [
                    CompletionItem(incomplete, type="file"),
                    *(CompletionItem(n) for n in names),
                ]
        elif prompts[0] in UTIL_SUBCOMMANDS:
            from .util import main as util_main

            return ShellComplete(util_main, {}, "gptme-util", "").get_completions(
                prompts, incomplete
            )
    return [CompletionItem(incomplete, type="file")]
