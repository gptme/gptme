from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import threading
from collections.abc import Generator
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..logmanager import Log

from gptme.config import get_config
from gptme.constants import INTERRUPT_CONTENT

from ..message import Message
from ..telemetry import trace_function
from ..util.interrupt import clear_interruptible
from ..util.terminal import terminal_state_title
from .base import (
    ConfirmFunc,
    Parameter,
    ToolFormat,
    ToolSpec,
    ToolUse,
    get_tool_format,
    set_tool_format,
)

logger = logging.getLogger(__name__)


__all__ = [
    # types
    "ToolSpec",
    "ToolUse",
    "ToolFormat",
    "Parameter",
    "ConfirmFunc",
    # functions
    "get_tool_format",
    "set_tool_format",
]

# Context-local storage for tools
# Each context (thread/async task) gets its own independent copy of tool state
_loaded_tools_var: ContextVar[list[ToolSpec] | None] = ContextVar(
    "loaded_tools", default=None
)
_available_tools_var: ContextVar[list[ToolSpec] | None] = ContextVar(
    "available_tools", default=None
)

# Note: Tools must be initialized in each context that needs them.
# This is particularly important for server environments where request handling
# happens in different contexts than where tools were initially loaded.


def _get_loaded_tools() -> list[ToolSpec]:
    tools = _loaded_tools_var.get()
    if tools is None:
        tools = []
        _loaded_tools_var.set(tools)
    return tools


def _get_available_tools_cache() -> list[ToolSpec] | None:
    return _available_tools_var.get()


def _set_available_tools_cache(tools: list[ToolSpec] | None) -> None:
    _available_tools_var.set(tools)


def _discover_tools(module_names: list[str]) -> list[ToolSpec]:
    """Discover tools in a package or module, given the module/package name as a string."""
    tools = []
    for module_name in module_names:
        try:
            # Dynamically import the package or module
            module = importlib.import_module(module_name)
        except ModuleNotFoundError:
            logger.warning("Module or package %s not found", module_name)
            continue

        modules = []
        # Check if it's a package or a module
        if hasattr(module, "__path__"):  # It's a package
            # Iterate over modules in the package
            for _, submodule_name, _ in pkgutil.iter_modules(module.__path__):
                # Skip private modules
                if submodule_name.startswith("_"):
                    continue
                full_submodule_name = f"{module_name}.{submodule_name}"
                try:
                    modules.append(importlib.import_module(full_submodule_name))
                except ModuleNotFoundError as e:
                    logger.warning(
                        "Missing dependency '%s' for module %s",
                        e.name,
                        full_submodule_name,
                    )
                    continue
        else:  # It's a single module
            modules.append(module)

        # Find instances of ToolSpec in the modules
        for module in modules:
            for _, obj in inspect.getmembers(module, lambda c: isinstance(c, ToolSpec)):
                tools.append(obj)

    return tools


# Global lock for thread-safe tool initialization
_tools_init_lock = threading.Lock()


def init_tools(
    allowlist: list[str] | None = None,
) -> list[ToolSpec]:
    """Initialize tools in a thread-safe manner.

    This function is thread-safe and can be called from multiple threads.
    Each thread will get its own copy of the tools.

    If allowlist is not provided, it will be loaded from the environment variable
    TOOL_ALLOWLIST or the chat config (if set).
    """
    with _tools_init_lock:
        loaded_tools = _get_loaded_tools()
        config = get_config()

        if allowlist is None:
            env_allowlist = config.get_env("TOOL_ALLOWLIST")
            if env_allowlist:
                allowlist = env_allowlist.split(",")
            elif config.chat and config.chat.tools:
                allowlist = config.chat.tools

        for tool in get_toolchain(allowlist):
            if has_tool(tool.name):
                continue
            if tool.init:
                tool = tool.init()

            # Register tool's hooks and commands
            tool.register_hooks()
            tool.register_commands()

            loaded_tools.append(tool)

        for tool_name in allowlist or []:
            if not has_tool(tool_name):
                # if the tool is found but unavailable, we log a warning
                if tool_name in [tool.name for tool in get_available_tools()]:
                    logger.warning("Tool %s found but is unavailable", tool_name)
                    continue
                raise ValueError(f"Tool '{tool_name}' not found")

        return loaded_tools


def get_toolchain(allowlist: list[str] | None) -> list[ToolSpec]:
    # Validate allowlist if provided
    # TODO: maybe check in CLI init instead, as this might hard error in the server when loading conversations where tools are not available
    if allowlist is not None:
        available_tools = get_available_tools()
        available_tool_names = [tool.name for tool in available_tools]

        for tool_name in allowlist:
            if tool_name not in available_tool_names:
                raise ValueError(
                    f"Tool '{tool_name}' not found. Available tools: {', '.join(sorted(available_tool_names))}"
                )

            # Check if tool is available
            tool_obj = next(tool for tool in available_tools if tool.name == tool_name)
            if not tool_obj.is_available:
                raise ValueError(
                    f"Tool '{tool_name}' is unavailable (likely missing dependencies)"
                )

    tools = []
    for tool in get_available_tools():
        if allowlist is not None and not tool.is_mcp and tool.name not in allowlist:
            continue
        if not tool.is_available:
            continue
        if tool.disabled_by_default:
            if allowlist is None or tool.name not in allowlist:
                continue
        tools.append(tool)
    return tools


@trace_function(name="tools.execute_msg", attributes={"component": "tools"})
def execute_msg(
    msg: Message,
    confirm: ConfirmFunc,
    log: Log | None = None,
    workspace: Path | None = None,
) -> Generator[Message, None, None]:
    """Uses any tools called in a message and returns the response."""
    assert msg.role == "assistant", "Only assistant messages can be executed"

    for tooluse in ToolUse.iter_from_content(msg.content):
        if tooluse.is_runnable:
            with terminal_state_title("🛠️ running {tooluse.tool}"):
                try:
                    for tool_response in tooluse.execute(confirm, log, workspace):
                        yield tool_response.replace(call_id=tooluse.call_id)
                except KeyboardInterrupt:
                    clear_interruptible()
                    yield Message(
                        "system",
                        INTERRUPT_CONTENT,
                        call_id=tooluse.call_id,
                    )
                    break


def get_tool_for_langtag(lang: str) -> ToolSpec | None:
    """Get the tool that handles a given language tag.

    Called often when checking streaming output for executable blocks.
    Not cached since tools are thread-local and caching would be complex/brittle.
    """
    block_type = lang.split(" ")[0]
    for tool in _get_loaded_tools():
        if block_type in tool.block_types:
            return tool
    return None


def is_supported_langtag(lang: str) -> bool:
    return bool(get_tool_for_langtag(lang))


def get_available_tools(include_mcp: bool = True) -> list[ToolSpec]:
    from gptme.plugins import get_plugin_tool_modules
    from gptme.tools.mcp_adapter import create_mcp_tools  # fmt: skip

    # Only use cache if we want MCP tools (cache always includes MCP)
    available_tools = _get_available_tools_cache() if include_mcp else None

    if available_tools is None:
        # We need to load tools first
        config = get_config()

        tool_modules: list[str] = list()
        env_tool_modules = config.get_env("TOOL_MODULES", "gptme.tools")

        if env_tool_modules:
            tool_modules = env_tool_modules.split(",")

        # Add plugin tool modules
        if config.project and config.project.plugins.paths:
            from pathlib import Path

            # Resolve plugin paths (support ~ and relative paths)
            plugin_paths = []
            for path_str in config.project.plugins.paths:
                path = Path(path_str).expanduser()
                if not path.is_absolute() and config.project._workspace:
                    # Relative to workspace
                    path = config.project._workspace / path
                plugin_paths.append(path)

            # Get tool modules from plugins
            plugin_tool_modules = get_plugin_tool_modules(
                plugin_paths,
                enabled_plugins=config.project.plugins.enabled
                if config.project.plugins.enabled is not None
                else None,
            )
            tool_modules.extend(plugin_tool_modules)

        available_tools = sorted(_discover_tools(tool_modules))
        if include_mcp:
            available_tools.extend(create_mcp_tools(config))
            # Only cache if we included MCP tools
            _set_available_tools_cache(available_tools)
        else:
            # Don't cache partial results
            return available_tools

    return available_tools


def clear_tools():
    """Clear all context-local tool state."""
    _set_available_tools_cache(None)
    _loaded_tools_var.set([])


def get_tools() -> list[ToolSpec]:
    """Returns all loaded tools"""
    return _get_loaded_tools()


def get_tool(tool_name: str) -> ToolSpec | None:
    """Returns a loaded tool by name or block type."""
    loaded_tools = _get_loaded_tools()
    # check tool names
    for tool in loaded_tools:
        if tool.name == tool_name:
            return tool
    # check block types
    for tool in loaded_tools:
        if tool_name in tool.block_types:
            return tool
    return None


def has_tool(tool_name: str) -> bool:
    """Returns True if a tool is loaded."""
    for tool in _get_loaded_tools():
        if tool.name == tool_name:
            return True
    return False
