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

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

# Cache for discovered plugins to avoid repeated discovery
_plugin_cache: dict[tuple[Path, ...], list[Plugin]] = {}

# Track which plugins have been logged as loaded (to avoid duplicate logs)
_loaded_plugins: set[str] = set()

__all__ = [
    "Plugin",
    "discover_plugins",
    "get_plugin_tool_modules",
    "register_plugin_hooks",
    "register_plugin_commands",
    "detect_install_environment",
    "get_install_instructions",
]


@dataclass
class Plugin:
    """Represents a discovered plugin with its components."""

    name: str
    path: Path

    # Module names for discovered components
    tool_modules: list[str] = field(default_factory=list)
    hook_modules: list[str] = field(default_factory=list)
    command_modules: list[str] = field(default_factory=list)


def discover_plugins(plugin_paths: list[Path]) -> list[Plugin]:
    """
    Discover plugins from the given search paths with smart src/ layout detection.

    For each path, tries in order:
    1. If path itself is a plugin (has __init__.py + tools/hooks/commands), load it
    2. If path has pyproject.toml + src/ subdirectory, search src/ for plugins
    3. Otherwise search for plugin directories in the path

    A valid plugin is a directory containing:
    - __init__.py (makes it a Python package)
    - At least one of: tools/, hooks/, commands/ subdirectories

    Args:
        plugin_paths: List of paths to search for plugins

    Returns:
        List of discovered Plugin instances
    """
    # Check cache first (keyed by resolved paths)
    cache_key = tuple(p.resolve() for p in plugin_paths)
    if cache_key in _plugin_cache:
        return _plugin_cache[cache_key]

    plugins: list[Plugin] = []

    for search_path in plugin_paths:
        if not search_path.exists():
            logger.debug(f"Plugin search path does not exist: {search_path}")
            continue

        if not search_path.is_dir():
            logger.warning(f"Plugin search path is not a directory: {search_path}")
            continue

        logger.debug(f"Searching for plugins in: {search_path}")

        # Strategy 1: Check if path itself is a plugin
        if _is_plugin_dir(search_path):
            plugin = _load_plugin(search_path)
            if plugin:
                plugins.append(plugin)
                logger.debug(f"Discovered plugin: {plugin.name}")
            continue

        # Strategy 2: Check for src/ layout (pyproject.toml + src/ directory)
        if (search_path / "pyproject.toml").exists() and (search_path / "src").exists():
            logger.debug(f"Detected src/ layout in {search_path}")
            src_dir = search_path / "src"
            for plugin_dir in src_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue
                if _is_plugin_dir(plugin_dir):
                    plugin = _load_plugin(plugin_dir)
                    if plugin:
                        plugins.append(plugin)
                        logger.debug(f"Discovered plugin: {plugin.name}")
            continue

        # Strategy 3: Search for plugin directories in the path
        for plugin_dir in search_path.iterdir():
            if not plugin_dir.is_dir():
                continue
            if _is_plugin_dir(plugin_dir):
                plugin = _load_plugin(plugin_dir)
                if plugin:
                    plugins.append(plugin)
                    logger.debug(f"Discovered plugin: {plugin.name}")

    # Cache for subsequent calls
    _plugin_cache[cache_key] = plugins
    return plugins


def _is_plugin_dir(path: Path) -> bool:
    """
    Check if a directory is a valid plugin.

    A valid plugin must be a Python package (__init__.py exists).
    Component directories (tools/, hooks/, commands/) are optional.
    """
    return (path / "__init__.py").exists()


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
            # tools/ is a package, check if it contains any tool files
            tool_files = [
                f
                for f in tools_dir.glob("*.py")
                if f.name != "__init__.py" and not f.name.startswith("_")
            ]
            if tool_files:
                # Has actual tool files, register the package module
                plugin.tool_modules.append(f"{plugin_name}.tools")
                logger.debug(f"  Found tools package: {plugin_name}.tools")
            else:
                logger.debug(f"  Skipping empty tools package: {plugin_name}.tools")
        else:
            # tools/ is a directory with individual modules
            for tool_file in tools_dir.glob("*.py"):
                if tool_file.name.startswith("_"):
                    continue
                module_name = f"{plugin_name}.tools.{tool_file.stem}"
                plugin.tool_modules.append(module_name)
                logger.debug(f"  Found tool module: {module_name}")

    # Discover hooks
    hooks_dir = plugin_path / "hooks"
    if hooks_dir.exists() and hooks_dir.is_dir():
        if (hooks_dir / "__init__.py").exists():
            # hooks/ is a package, check if it contains any hook files
            hook_files = [
                f
                for f in hooks_dir.glob("*.py")
                if f.name != "__init__.py" and not f.name.startswith("_")
            ]
            if hook_files:
                # Has actual hook files, register the package module
                plugin.hook_modules.append(f"{plugin_name}.hooks")
                logger.debug(f"  Found hooks package: {plugin_name}.hooks")
            else:
                logger.debug(f"  Skipping empty hooks package: {plugin_name}.hooks")
        else:
            # hooks/ is a directory with individual modules
            for hook_file in hooks_dir.glob("*.py"):
                if hook_file.name.startswith("_"):
                    continue
                module_name = f"{plugin_name}.hooks.{hook_file.stem}"
                plugin.hook_modules.append(module_name)
                logger.debug(f"  Found hook module: {module_name}")

    # Discover commands
    commands_dir = plugin_path / "commands"
    if commands_dir.exists() and commands_dir.is_dir():
        if (commands_dir / "__init__.py").exists():
            # commands/ is a package, check if it contains any command files
            command_files = [
                f
                for f in commands_dir.glob("*.py")
                if f.name != "__init__.py" and not f.name.startswith("_")
            ]
            if command_files:
                # Has actual command files, register the package module
                plugin.command_modules.append(f"{plugin_name}.commands")
                logger.debug(f"  Found commands package: {plugin_name}.commands")
            else:
                logger.debug(
                    f"  Skipping empty commands package: {plugin_name}.commands"
                )
        else:
            # commands/ is a directory with individual modules
            for command_file in commands_dir.glob("*.py"):
                if command_file.name.startswith("_"):
                    continue
                module_name = f"{plugin_name}.commands.{command_file.stem}"
                plugin.command_modules.append(module_name)
                logger.debug(f"  Found command module: {module_name}")

    return plugin


def _mark_plugin_loaded(plugin: Plugin) -> bool:
    """Mark a plugin as loaded, return True if newly loaded."""
    if plugin.name not in _loaded_plugins:
        _loaded_plugins.add(plugin.name)
        return True
    return False


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
    newly_loaded: list[str] = []
    for plugin in plugins:
        # Apply allowlist if provided
        if enabled_plugins and plugin.name not in enabled_plugins:
            logger.debug(f"Skipping plugin {plugin.name}: not in allowlist")
            continue

        # Track newly loaded plugins
        # Track newly loaded plugins
        if _mark_plugin_loaded(plugin):
            newly_loaded.append(plugin.name)

        # Add plugin's tool modules
        tool_modules.extend(plugin.tool_modules)

    # Log all newly loaded plugins in one line
    if newly_loaded:
        console.log(
            f"Using plugins {', '.join(f'[green]{plugin}[/green]' for plugin in newly_loaded)}"
        )

    if tool_modules:
        logger.debug(f"Loaded {len(tool_modules)} tool modules")
    return tool_modules


def register_plugin_hooks(
    plugin_paths: list[Path],
    enabled_plugins: list[str] | None = None,
) -> None:
    """
    Register hooks from all enabled plugins.

    Discovers plugins, imports their hook modules, and calls their register()
    functions to register hooks with the gptme hook system.

    Args:
        plugin_paths: Paths to search for plugins
        enabled_plugins: Optional allowlist of plugin names (None = all)
    """
    plugins = discover_plugins(plugin_paths)

    hooks_registered = 0
    for plugin in plugins:
        # Apply allowlist if provided
        if enabled_plugins and plugin.name not in enabled_plugins:
            logger.debug(f"Skipping plugin {plugin.name}: not in allowlist")
            continue

        # Mark plugin as loaded (once across all component types)
        if plugin.hook_modules:
            _mark_plugin_loaded(plugin)

        # Register hooks from each module
        for module_name in plugin.hook_modules:
            try:
                # Import the hook module
                module = __import__(module_name, fromlist=["register"])

                # Call the module's register() function if it exists
                if hasattr(module, "register"):
                    module.register()
                    hooks_registered += 1
                    logger.debug(f"Registered hooks from {module_name}")
                else:
                    logger.warning(
                        f"Hook module {module_name} has no register() function"
                    )

            except Exception as e:
                logger.error(f"Failed to register hooks from {module_name}: {e}")

    if hooks_registered:
        logger.debug(f"Registered {hooks_registered} hook modules from plugins")


def register_plugin_commands(
    plugin_paths: list[Path],
    enabled_plugins: list[str] | None = None,
) -> None:
    """
    Register commands from all enabled plugins.

    Discovers plugins, imports their command modules, and calls their register()
    functions to register commands with the gptme command system.

    Args:
        plugin_paths: Paths to search for plugins
        enabled_plugins: Optional allowlist of plugin names (None = all)
    """
    plugins = discover_plugins(plugin_paths)

    commands_registered = 0
    for plugin in plugins:
        # Apply allowlist if provided
        if enabled_plugins and plugin.name not in enabled_plugins:
            logger.debug(f"Skipping plugin {plugin.name}: not in allowlist")
            continue

        # Mark plugin as loaded (once across all component types)
        if plugin.command_modules:
            _mark_plugin_loaded(plugin)

        # Register commands from each module
        for module_name in plugin.command_modules:
            try:
                # Import the command module
                module = __import__(module_name, fromlist=["register"])

                # Call the module's register() function if it exists
                if hasattr(module, "register"):
                    module.register()
                    commands_registered += 1
                    logger.debug(f"Registered commands from {module_name}")
                else:
                    logger.warning(
                        f"Command module {module_name} has no register() function"
                    )

            except Exception as e:
                logger.error(f"Failed to register commands from {module_name}: {e}")

    if commands_registered:
        logger.debug(f"Registered {commands_registered} command modules from plugins")


def detect_install_environment() -> str:
    """
    Detect how gptme is installed.

    Returns:
        Environment type: 'pipx', 'uvx', 'venv', or 'system'
    """
    import os

    # Check for pipx
    if os.environ.get("PIPX_HOME") or "pipx/venvs" in sys.prefix:
        return "pipx"

    # Check for uvx (comprehensive cross-platform detection to avoid false positives)
    if (
        "/.uv/" in sys.prefix
        or "\\.uv\\" in sys.prefix
        or "/uv/" in sys.prefix
        or "\\uv\\" in sys.prefix
        or sys.prefix.endswith("/.uv")
        or sys.prefix.endswith("\\.uv")
        or sys.prefix.endswith("/uv")
        or sys.prefix.endswith("\\uv")
        or os.environ.get("UV_HOME")
    ):
        return "uvx"

    # Check for virtualenv
    if sys.prefix != sys.base_prefix:
        return "venv"

    return "system"


def get_install_instructions(plugin_path: Path, env_type: str | None = None) -> str:
    """
    Get installation instructions for a plugin based on the environment.

    Args:
        plugin_path: Path to the plugin directory (with pyproject.toml)
        env_type: Environment type ('pipx', 'uvx', 'venv', 'system').
                  If None, auto-detects using detect_install_environment()

    Returns:
        Installation command string
    """
    if env_type is None:
        env_type = detect_install_environment()

    if env_type == "pipx":
        return f"pipx inject gptme {plugin_path}"
    elif env_type == "uvx":
        # Note: uvx doesn't have inject yet, may need different approach
        return f"uv pip install --system -e {plugin_path}"
    elif env_type == "venv":
        return f"pip install -e {plugin_path}"
    else:  # system
        return f"pip install --user -e {plugin_path}"
