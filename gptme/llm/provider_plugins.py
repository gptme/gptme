"""Discovery and loading of third-party LLM provider plugins via entry points.

Third-party packages can register providers by adding an entry point in the
``gptme.providers`` group::

    [project.entry-points."gptme.providers"]
    minimax = "gptme_provider_minimax:provider"

Where ``provider`` is a :class:`~gptme.llm.models.types.ProviderPlugin` instance.
"""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def discover_provider_plugins() -> tuple:
    """Discover provider plugins registered via the ``gptme.providers`` entry point group.

    Results are cached after the first call.  Use :func:`clear_plugin_cache` in
    tests or when reloading plugins at runtime.

    Returns:
        Cached tuple of :class:`~gptme.llm.models.types.ProviderPlugin` instances.
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
    discover_provider_plugins.cache_clear()


def get_provider_plugin(name: str):
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
