"""
Plugin system for gptme.

Provides a simple folder-based plugin discovery mechanism where each plugin
is a directory containing:
- __init__.py (plugin metadata)
- tools/ (optional: tool modules)
- hooks/ (optional: hook modules)
- commands/ (optional: command modules - future)

Plugins are discovered from configured paths and loaded similarly to how
custom tools currently work via TOOL_MODULES.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "Plugin",
    "discover_plugins",
    "get_plugin_tool_modules",
]


@dataclass
class Plugin:
    """Represents a discovered plugin with its components."""

    name: str
    path: Path

    # Module names for discovered components
    tool_modules: list[str] = field(default_factory=list)
    # Future: hook_modules, command_modules


def discover_plugins(plugin_paths: list[Path]) -> list[Plugin]:
    """
    Discover plugins from the given search paths.

    A valid plugin is a directory containing:
    - __init__.py (makes it a Python package)
    - Optionally: tools/, hooks/, commands/ subdirectories

    Args:
        plugin_paths: List of paths to search for plugins

    Returns:
        List of discovered Plugin instances
    """
    plugins: list[Plugin] = []

    for search_path in plugin_paths:
        if not search_path.exists():
            logger.debug(f"Plugin search path does not exist: {search_path}")
            continue

        if not search_path.is_dir():
            logger.warning(f"Plugin search path is not a directory: {search_path}")
            continue

        logger.debug(f"Searching for plugins in: {search_path}")

        # Find all directories with __init__.py (valid Python packages)
        for plugin_dir in search_path.iterdir():
            if not plugin_dir.is_dir():
                continue

            # Check for __init__.py to ensure it's a valid Python package
            if not (plugin_dir / "__init__.py").exists():
                logger.debug(f"Skipping {plugin_dir.name}: not a Python package")
                continue

            # Discover plugin components
            plugin = _load_plugin(plugin_dir)
            if plugin:
                plugins.append(plugin)
                logger.info(f"Discovered plugin: {plugin.name}")

    return plugins


def _load_plugin(plugin_path: Path) -> Plugin | None:
    """
    Load a plugin from a directory.

    Inspects the plugin directory structure and discovers available components.
    """
    plugin_name = plugin_path.name

    # Ensure plugin is importable by adding parent to sys.path if needed
    plugin_parent = plugin_path.parent
    if str(plugin_parent) not in sys.path:
        sys.path.insert(0, str(plugin_parent))

    plugin = Plugin(name=plugin_name, path=plugin_path)

    # Discover tools
    tools_dir = plugin_path / "tools"
    if tools_dir.exists() and tools_dir.is_dir():
        if (tools_dir / "__init__.py").exists():
            # tools/ is a package, register the package module
            plugin.tool_modules.append(f"{plugin_name}.tools")
            logger.debug(f"  Found tools package: {plugin_name}.tools")
        else:
            # tools/ is a directory with individual modules
            for tool_file in tools_dir.glob("*.py"):
                if tool_file.name.startswith("_"):
                    continue
                module_name = f"{plugin_name}.tools.{tool_file.stem}"
                plugin.tool_modules.append(module_name)
                logger.debug(f"  Found tool module: {module_name}")

    # Future: Discover hooks similarly
    # hooks_dir = plugin_path / "hooks"
    # if hooks_dir.exists() and hooks_dir.is_dir():
    #     plugin.hook_modules = _discover_hook_modules(plugin_name, hooks_dir)

    # Future: Discover commands similarly
    # commands_dir = plugin_path / "commands"
    # if commands_dir.exists() and commands_dir.is_dir():
    #     plugin.command_modules = _discover_command_modules(plugin_name, commands_dir)

    return plugin


def get_plugin_tool_modules(
    plugin_paths: list[Path],
    enabled_plugins: list[str] | None = None,
) -> list[str]:
    """
    Get tool module names from all enabled plugins.

    This integrates with the existing tool discovery system by returning
    module names that can be passed to _discover_tools().

    Args:
        plugin_paths: Paths to search for plugins
        enabled_plugins: Optional allowlist of plugin names (None = all)

    Returns:
        List of module names containing tools (e.g., "my_plugin.tools")
    """
    plugins = discover_plugins(plugin_paths)

    tool_modules: list[str] = []
    for plugin in plugins:
        # Apply allowlist if provided
        if enabled_plugins and plugin.name not in enabled_plugins:
            logger.debug(f"Skipping plugin {plugin.name}: not in allowlist")
            continue

        # Add plugin's tool modules
        tool_modules.extend(plugin.tool_modules)

    logger.info(f"Loaded {len(tool_modules)} tool modules from {len(plugins)} plugins")
    return tool_modules
