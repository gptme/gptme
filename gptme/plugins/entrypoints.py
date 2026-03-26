"""Entry-point discovery for unified gptme plugins.

Third-party packages register plugins via the ``gptme.plugins`` entry-point
group in their ``pyproject.toml``::

    [project.entry-points."gptme.plugins"]
    my_plugin = "my_package:plugin"

Where ``plugin`` is a :class:`~gptme.plugins.plugin.GptmePlugin` instance.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.metadata import entry_points

from .plugin import GptmePlugin

logger = logging.getLogger(__name__)

ENTRYPOINT_GROUP = "gptme.plugins"


@lru_cache(maxsize=1)
def discover_entrypoint_plugins() -> tuple[GptmePlugin, ...]:
    """Discover plugins registered via the ``gptme.plugins`` entry-point group.

    Results are cached after the first call.  Use :func:`clear_entrypoint_cache`
    in tests or when reloading plugins at runtime.
    """
    plugins: list[GptmePlugin] = []
    for ep in entry_points(group=ENTRYPOINT_GROUP):
        try:
            obj = ep.load()
        except Exception as exc:
            logger.warning("Failed to load plugin %r: %s", ep.name, exc)
            continue

        if isinstance(obj, GptmePlugin):
            plugins.append(obj)
            logger.debug("Loaded entry-point plugin: %s", obj.name)
        elif callable(obj):
            # Support factory functions that return GptmePlugin
            try:
                result = obj()
                if isinstance(result, GptmePlugin):
                    plugins.append(result)
                    logger.debug("Loaded entry-point plugin: %s", result.name)
                else:
                    logger.warning(
                        "Plugin factory %r returned %r instead of GptmePlugin; skipping",
                        ep.name,
                        type(result).__name__,
                    )
            except Exception as exc:
                logger.warning("Plugin factory %r raised: %s", ep.name, exc)
        else:
            logger.warning(
                "Plugin %r exported %r instead of GptmePlugin; skipping",
                ep.name,
                type(obj).__name__,
            )

    return tuple(plugins)


def clear_entrypoint_cache() -> None:
    """Clear the entry-point plugin discovery cache."""
    discover_entrypoint_plugins.cache_clear()
