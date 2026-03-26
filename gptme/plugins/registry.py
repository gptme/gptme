"""Unified plugin registry.

Merges plugins from all discovery mechanisms into a single list:
1. Folder-based plugins (from configured paths)
2. Entry-point plugins (``gptme.plugins`` group)
3. Legacy provider entry points (``gptme.providers`` group, backward compat)
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from .entrypoints import discover_entrypoint_plugins
from .plugin import GptmePlugin

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_all_plugins: list[GptmePlugin] = []
_initialized = False
_initialized_plugins: set[str] = set()


def get_all_plugins() -> list[GptmePlugin]:
    """Return all discovered plugins.

    Returns an empty list if :func:`discover_all_plugins` has not been called.
    """
    return list(_all_plugins)


def discover_all_plugins(
    folder_paths: list[Path] | None = None,
    enabled_plugins: list[str] | None = None,
) -> list[GptmePlugin]:
    """Run all discovery mechanisms and merge results.

    Args:
        folder_paths: Paths to search for folder-based plugins.
        enabled_plugins: Optional allowlist of plugin names (None = all).

    Returns:
        List of all discovered :class:`GptmePlugin` instances.
    """
    global _initialized
    plugins: list[GptmePlugin] = []

    # 1. Folder plugins
    if folder_paths:
        from . import discover_plugins

        plugins.extend(
            _folder_plugin_to_gptme_plugin(fp) for fp in discover_plugins(folder_paths)
        )

    # 2. Entry-point plugins (new gptme.plugins group)
    # Dedup against folder plugins — an editable-install (pip install -e .) will
    # register the same plugin both as a folder plugin and an entry-point plugin.
    folder_names = {p.name for p in plugins}
    for ep_plugin in discover_entrypoint_plugins():
        if ep_plugin.name in folder_names:
            # Warn if the entry-point version has capabilities the folder adapter doesn't carry
            if ep_plugin.provider or ep_plugin.tools:
                logger.debug(
                    "Folder plugin %r shadows entry-point plugin with non-empty "
                    "provider/tools — those capabilities will be skipped. "
                    "If this is not an editable-install, update your plugin manifest.",
                    ep_plugin.name,
                )
        else:
            plugins.append(ep_plugin)

    # 3. Legacy provider entry points (gptme.providers)
    # Only include if not already registered via gptme.plugins
    known_names = {p.name for p in plugins}
    plugins.extend(
        p for p in _discover_legacy_provider_plugins() if p.name not in known_names
    )

    # Apply allowlist
    if enabled_plugins is not None:
        plugins = [p for p in plugins if p.name in enabled_plugins]

    # Call plugin-level init (deferred to avoid circular import)
    from ..config import get_config

    config = get_config()
    for p in plugins:
        if p.init and p.name not in _initialized_plugins:
            try:
                p.init(config)
                _initialized_plugins.add(p.name)
            except Exception as exc:
                logger.warning("Plugin %r init failed: %s", p.name, exc)

    _all_plugins[:] = plugins
    _initialized = True
    return plugins


def clear_registry() -> None:
    """Clear the plugin registry.  Useful in tests."""
    global _initialized
    _all_plugins.clear()
    _initialized_plugins.clear()
    _initialized = False
    # Also clear the entrypoints LRU cache so re-discovery picks up changes.
    from .entrypoints import clear_entrypoint_cache

    clear_entrypoint_cache()


def _folder_plugin_to_gptme_plugin(plugin: Plugin) -> GptmePlugin:
    """Adapt a folder-based Plugin to the unified GptmePlugin interface."""
    return GptmePlugin(
        name=plugin.name,
        tool_modules=list(plugin.tool_modules),
        register_hooks=_make_hook_registrar(plugin.hook_modules)
        if plugin.hook_modules
        else None,
        register_commands=_make_command_registrar(plugin.command_modules)
        if plugin.command_modules
        else None,
    )


def _make_hook_registrar(hook_modules: list[str]):
    """Create a callable that registers hooks from the given module names."""
    hook_modules = list(hook_modules)  # defensive copy

    def registrar():
        for module_name in hook_modules:
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "register"):
                    module.register()
                    logger.debug("Registered hooks from %s", module_name)
                else:
                    logger.warning(
                        "Hook module %s has no register() function", module_name
                    )
            except Exception as exc:
                logger.error("Failed to register hooks from %s: %s", module_name, exc)

    return registrar


def _make_command_registrar(command_modules: list[str]):
    """Create a callable that registers commands from the given module names."""
    command_modules = list(command_modules)  # defensive copy

    def registrar():
        for module_name in command_modules:
            try:
                module = importlib.import_module(module_name)
                if hasattr(module, "register"):
                    module.register()
                    logger.debug("Registered commands from %s", module_name)
                else:
                    logger.warning(
                        "Command module %s has no register() function", module_name
                    )
            except Exception as exc:
                logger.error(
                    "Failed to register commands from %s: %s", module_name, exc
                )

    return registrar


def _discover_legacy_provider_plugins() -> list[GptmePlugin]:
    """Bridge: wrap legacy ``gptme.providers`` entry points as GptmePlugin."""
    from ..llm.provider_plugins import _discover_provider_plugins_raw

    return [
        GptmePlugin(name=pp.name, provider=pp)
        for pp in _discover_provider_plugins_raw()
    ]


# Avoid circular import: Plugin is only used in type hints for the adapter
if TYPE_CHECKING:
    from . import Plugin
