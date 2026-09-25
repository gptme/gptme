"""Entry-point discovery for unified gptme plugins.

Third-party packages register plugins via the ``gptme.plugins`` entry-point
group in their ``pyproject.toml``::

    [project.entry-points."gptme.plugins"]
    my_plugin = "my_package:plugin"

Where ``plugin`` is a :class:`~gptme.plugins.plugin.GptmePlugin` instance.
"""

from __future__ import annotations

import logging
import time
from functools import cache
from importlib.metadata import entry_points

from .plugin import GptmePlugin

logger = logging.getLogger(__name__)

ENTRYPOINT_GROUP = "gptme.plugins"

# Loading an entry point imports its whole package; warn when that stalls
# startup so the culprit plugin is visible to the user.
_SLOW_LOAD_THRESHOLD = 1.0


def _normalize(name: str) -> str:
    return name.replace("-", "_").lower()


@cache
def _discover_entrypoint_plugins_cached(
    enabled: frozenset[str] | None = None,
) -> tuple[tuple[GptmePlugin, ...], tuple[tuple[str, Exception], ...]]:
    """Cached worker for :func:`discover_entrypoint_plugins`.

    Returns ``(plugins, failures)``, where ``failures`` are
    ``(entry_point_name, exception)`` pairs for entry points that could not be
    loaded or coerced into a plugin. Failures are part of the cached result so
    :func:`discover_entrypoint_plugins` can hand them to callers that want to
    surface them (gptme-doctor) without re-running discovery.
    """
    enabled_normalized = (
        {_normalize(name) for name in enabled} if enabled is not None else None
    )
    plugins: list[GptmePlugin] = []
    failures: list[tuple[str, Exception]] = []
    for ep in entry_points(group=ENTRYPOINT_GROUP):
        # Match on entry-point name or top-level package name, so a plugin
        # enabled under either (e.g. "gptme-tts" for entry point "tts" in
        # package "gptme_tts") is still loaded. The manifest name is only
        # known after ep.load(), which is exactly what we're avoiding here.
        ep_names = {_normalize(ep.name), _normalize(ep.module.split(".")[0])}
        if enabled_normalized is not None and not (ep_names & enabled_normalized):
            logger.debug("Skipping entry-point plugin %r: not enabled", ep.name)
            continue
        try:
            start = time.monotonic()
            obj = ep.load()
            duration = time.monotonic() - start
            if duration > _SLOW_LOAD_THRESHOLD:
                logger.warning(
                    "Plugin %r took %.1fs to load — its package imports heavy "
                    "dependencies at import time; consider deferring them",
                    ep.name,
                    duration,
                )
        except Exception as exc:
            logger.warning("Failed to load plugin %r: %s", ep.name, exc)
            failures.append((ep.name, exc))
            continue

        plugin = _coerce_to_plugin(ep.name, obj, errors=failures)
        if plugin is not None:
            plugins.append(plugin)
            logger.debug("Loaded entry-point plugin: %s", plugin.name)

    return tuple(plugins), tuple(failures)


def discover_entrypoint_plugins(
    enabled: frozenset[str] | None = None,
    errors: list[tuple[str, Exception]] | None = None,
) -> tuple[GptmePlugin, ...]:
    """Discover plugins registered via the ``gptme.plugins`` entry-point group.

    Args:
        enabled: Optional allowlist of plugin names. Entry points that match
            neither by entry-point name nor by top-level package name
            (dash/underscore-insensitive) are skipped *without being
            imported* — ``ep.load()`` imports the plugin's whole package,
            which can take seconds for plugins with heavy dependencies.
            Plugins whose manifest name differs from both must be enabled
            under their entry-point or package name.
        errors: Optional sink. When given, every entry point that failed to
            load or could not be coerced into a plugin is appended as
            ``(entry_point_name, exception)``. Tolerant runtime callers omit
            it; diagnostics (gptme-doctor) pass a list so a broken installed
            plugin is visible instead of silently dropped.

    Results are cached after the first call.  Use :func:`clear_entrypoint_cache`
    in tests or when reloading plugins at runtime.
    """
    plugins, failures = _discover_entrypoint_plugins_cached(enabled)
    if errors is not None:
        errors.extend(failures)
    return plugins


def _coerce_to_plugin(
    name: str,
    obj: object,
    _from_factory: bool = False,
    *,
    errors: list[tuple[str, Exception]] | None = None,
):
    """Normalize an entry-point export into a :class:`GptmePlugin`.

    Accepts a ``GptmePlugin``, a bare ``ToolSpec``, a list/tuple of ``ToolSpec``
    (wrapped into a plugin named after the entry point), or a zero-arg factory
    returning any of those. Returns ``None`` (with a warning) for anything else.

    Many existing plugins export a ``ToolSpec`` directly (``pkg:tool``) rather
    than a manifest, so accepting that form keeps them working instead of being
    silently skipped.

    ``errors``, when given, receives ``(name, exception)`` for exports that
    could not be coerced — the reason is otherwise only logged.
    """
    from ..tools.base import ToolSpec

    if isinstance(obj, GptmePlugin):
        return obj
    if isinstance(obj, ToolSpec):
        return GptmePlugin(name=name, tools=[obj])
    if (
        isinstance(obj, list | tuple)
        and obj
        and all(isinstance(o, ToolSpec) for o in obj)
    ):
        return GptmePlugin(name=name, tools=list(obj))
    # A factory callable (but only resolve one level to avoid recursion loops)
    if callable(obj) and not _from_factory:
        try:
            return _coerce_to_plugin(name, obj(), _from_factory=True, errors=errors)
        except Exception as exc:
            logger.warning("Plugin factory %r raised: %s", name, exc)
            if errors is not None:
                errors.append((name, exc))
            return None
    logger.warning(
        "Plugin %r exported %r instead of GptmePlugin or ToolSpec; skipping",
        name,
        type(obj).__name__,
    )
    if errors is not None:
        errors.append(
            (name, ValueError(f"exported {type(obj).__name__}, not a plugin"))
        )
    return None


def clear_entrypoint_cache() -> None:
    """Clear the entry-point plugin discovery cache."""
    _discover_entrypoint_plugins_cached.cache_clear()
