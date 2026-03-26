"""Unified plugin interface for gptme.

Both folder-based plugins and entry-point plugins normalize into
:class:`GptmePlugin` instances, providing a single internal abstraction
for tools, hooks, commands, and providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..config import Config
    from ..llm.models.types import ProviderPlugin
    from ..tools.base import ToolSpec


@dataclass
class GptmePlugin:
    """Unified plugin manifest.

    A plugin can provide any combination of tools, hooks, commands, and
    LLM providers.  Both folder-based plugins (discovered from filesystem
    paths) and entry-point plugins (installed packages) produce instances
    of this class.

    For entry-point plugins, register via ``pyproject.toml``::

        [project.entry-points."gptme.plugins"]
        my_plugin = "my_package:plugin"

    Where ``plugin`` is a :class:`GptmePlugin` instance.
    """

    name: str
    """Unique plugin name."""

    # -- Provider capabilities (optional) --

    provider: ProviderPlugin | None = None
    """LLM provider definition.  When set, the plugin registers a custom
    LLM provider accessible as ``<provider.name>/<model>``."""

    # -- Tool capabilities (optional) --

    tool_modules: list[str] = field(default_factory=list)
    """Module names containing :class:`~gptme.tools.base.ToolSpec` instances.
    These are passed to the existing tool discovery system."""

    tools: list[ToolSpec] = field(default_factory=list)
    """Direct :class:`~gptme.tools.base.ToolSpec` instances.  Useful for
    entry-point plugins that want to provide tools without a separate module."""

    # -- Hook capabilities (optional) --

    register_hooks: Callable[[], None] | None = None
    """Callable that registers hooks via :func:`~gptme.hooks.registry.register_hook`.
    Called once during hook initialization."""

    # -- Command capabilities (optional) --

    register_commands: Callable[[], None] | None = None
    """Callable that registers commands via :func:`~gptme.commands.base.register_command`.
    Called once during command initialization."""

    # -- Plugin lifecycle (optional) --

    init: Callable[[Config], None] | None = None
    """Optional plugin-level initialization.  Called once at discovery time
    with the full :class:`~gptme.config.Config`, before subsystem init.
    Plugin-specific config is stored under ``[plugin.<name>]`` in the TOML
    config files and is accessible via::

        user_cfg = config.user.plugin.get("<name>", {})
        project_cfg = config.project.plugin.get("<name>", {}) if config.project else {}

    Note that ``config.project`` may be ``None`` when no project config is
    present, so always guard access with a ``if config.project`` check."""
