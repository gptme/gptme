"""Discovery and loading of third-party LLM provider plugins via entry points.

Third-party packages can register providers in two ways:

1. **Unified plugins** (recommended) — via the ``gptme.plugins`` group::

       [project.entry-points."gptme.plugins"]
       my_plugin = "my_package:plugin"

   Where ``plugin`` is a :class:`~gptme.plugins.plugin.GptmePlugin` with a
   ``provider`` field.

2. **Legacy provider plugins** — via the ``gptme.providers`` group::

       [project.entry-points."gptme.providers"]
       minimax = "gptme_provider_minimax:provider"

   Where ``provider`` is a :class:`~gptme.llm.models.types.ProviderPlugin` instance.
   This format is still supported for backward compatibility.

Both paths are merged by the unified plugin registry.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models.types import ProviderPlugin

logger = logging.getLogger(__name__)


def discover_provider_plugins() -> tuple[ProviderPlugin, ...]:
    """Return all provider plugins from the unified registry.

    This includes providers from both ``gptme.plugins`` and legacy
    ``gptme.providers`` entry-point groups, as well as folder-based plugins.

    Falls back to raw entry-point scanning if the registry has not been
    initialized yet (e.g. during early model resolution before ``init()``).
    """
    from ..plugins.registry import _initialized, get_all_plugins

    if _initialized:
        return tuple(p.provider for p in get_all_plugins() if p.provider is not None)

    # Fallback: registry not yet initialized, scan entry points directly
    return _discover_provider_plugins_raw()


@lru_cache(maxsize=1)
def _discover_provider_plugins_raw() -> tuple[ProviderPlugin, ...]:
    """Scan the legacy ``gptme.providers`` entry-point group directly.

    This is the raw scanner used by the unified registry as a source, and
    also serves as a fallback before the registry is initialized.

    Results are cached after the first call.  Use :func:`clear_plugin_cache`
    to reset.
    """
    from importlib.metadata import entry_points  # fmt: skip

    from .models.types import ProviderPlugin  # fmt: skip

    plugins: list[ProviderPlugin] = []
    for ep in entry_points(group="gptme.providers"):
        try:
            plugin = ep.load()
        except Exception as exc:
            logger.warning("Failed to load provider plugin %r: %s", ep.name, exc)
            continue

        if not isinstance(plugin, ProviderPlugin):
            logger.warning(
                "Provider plugin %r exported %r instead of a ProviderPlugin; skipping",
                ep.name,
                type(plugin).__name__,
            )
            continue

        plugins.append(plugin)
        logger.debug("Loaded provider plugin: %s", plugin.name)

    return tuple(plugins)


def clear_plugin_cache() -> None:
    """Clear the plugin discovery cache.

    Useful in tests or when dynamically installing/removing plugins at runtime.
    """
    _discover_provider_plugins_raw.cache_clear()

    from ..plugins.entrypoints import clear_entrypoint_cache
    from ..plugins.registry import clear_registry

    clear_entrypoint_cache()
    clear_registry()


def get_provider_plugin(name: str) -> ProviderPlugin | None:
    """Return the :class:`~gptme.llm.models.types.ProviderPlugin` with the given name.

    Returns ``None`` if no plugin with that name is registered.
    """
    for plugin in discover_provider_plugins():
        if plugin.name == name:
            return plugin
    return None


def is_plugin_provider(provider: str) -> bool:
    """Return ``True`` if *provider* matches a registered plugin provider name."""
    return get_provider_plugin(provider) is not None


def get_plugin_api_keys() -> dict[str, str]:
    """Return a mapping of ``{provider_name: api_key_env}`` for all plugin providers."""
    return {p.name: p.api_key_env for p in discover_provider_plugins()}
