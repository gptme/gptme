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

        plugin = _coerce_to_plugin(ep.name, obj)
        if plugin is not None:
            plugins.append(plugin)
            logger.debug("Loaded entry-point plugin: %s", plugin.name)

    return tuple(plugins)


def _coerce_to_plugin(name: str, obj: object, _from_factory: bool = False):
    """Normalize an entry-point export into a :class:`GptmePlugin`.

    Accepts a ``GptmePlugin``, a bare ``ToolSpec``, a list/tuple of ``ToolSpec``
    (wrapped into a plugin named after the entry point), or a zero-arg factory
    returning any of those. Returns ``None`` (with a warning) for anything else.

    Many existing plugins export a ``ToolSpec`` directly (``pkg:tool``) rather
    than a manifest, so accepting that form keeps them working instead of being
    silently skipped.
    """
    from ..tools.base import ToolSpec

    if isinstance(obj, GptmePlugin):
        return obj
    if isinstance(obj, ToolSpec):
        return GptmePlugin(name=name, tools=[obj])
    if (
        isinstance(obj, (list, tuple))
        and obj
        and all(isinstance(o, ToolSpec) for o in obj)
    ):
        return GptmePlugin(name=name, tools=list(obj))
    # A factory callable (but only resolve one level to avoid recursion loops)
    if callable(obj) and not _from_factory:
        try:
            return _coerce_to_plugin(name, obj(), _from_factory=True)
        except Exception as exc:
            logger.warning("Plugin factory %r raised: %s", name, exc)
            return None
    logger.warning(
        "Plugin %r exported %r instead of GptmePlugin or ToolSpec; skipping",
        name,
        type(obj).__name__,
    )
    return None


def clear_entrypoint_cache() -> None:
    """Clear the entry-point plugin discovery cache."""
    discover_entrypoint_plugins.cache_clear()
