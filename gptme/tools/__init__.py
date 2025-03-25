import importlib
import inspect
import logging
import pkgutil
from collections.abc import Generator
from functools import lru_cache

from gptme.config import get_config
from gptme.constants import INTERRUPT_CONTENT
from gptme.tools.mcp_adapter import create_mcp_tools

from ..message import Message
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

_loaded_tools: list[ToolSpec] = []
_available_tools: list[ToolSpec] | None = None


def _discover_tools(module_names: frozenset[str]) -> list[ToolSpec]:
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

    # Add MCP tools
    config = get_config()
    tools.extend(create_mcp_tools(config))

    return tools


@lru_cache
def init_tools(
    allowlist: frozenset[str] | None = None,
) -> None:
    """Runs initialization logic for tools."""

    config = get_config()

    if allowlist is None:
        env_allowlist = config.get_env("TOOL_ALLOWLIST")
        if env_allowlist:
            allowlist = frozenset(env_allowlist.split(","))

    for tool in get_available_tools():
        if tool in _loaded_tools:
            logger.warning("Tool '%s' already loaded", tool.name)
            continue
        if allowlist and tool.name not in allowlist:
            continue
        if not tool.available:
            continue
        if tool.disabled_by_default:
            if not allowlist or tool.name not in allowlist:
                continue
        if tool.init:
            tool = tool.init()

        _loaded_tools.append(tool)

    for tool_name in allowlist or []:
        if not has_tool(tool_name):
            raise ValueError(f"Tool '{tool_name}' not found")


def execute_msg(msg: Message, confirm: ConfirmFunc) -> Generator[Message, None, None]:
    """Uses any tools called in a message and returns the response."""
    assert msg.role == "assistant", "Only assistant messages can be executed"

    for tooluse in ToolUse.iter_from_content(msg.content):
        if tooluse.is_runnable:
            with terminal_state_title("🛠️ running {tooluse.tool}"):
                try:
                    for tool_response in tooluse.execute(confirm):
                        yield tool_response.replace(call_id=tooluse.call_id)
                except KeyboardInterrupt:
                    clear_interruptible()
                    yield Message(
                        "system",
                        INTERRUPT_CONTENT,
                        call_id=tooluse.call_id,
                    )
                    break


# Called often when checking streaming output for executable blocks,
# so we cache the result.
@lru_cache
def get_tool_for_langtag(lang: str) -> ToolSpec | None:
    block_type = lang.split(" ")[0]
    for tool in _loaded_tools:
        if block_type in tool.block_types:
            return tool
    return None


def is_supported_langtag(lang: str) -> bool:
    return bool(get_tool_for_langtag(lang))


def get_available_tools() -> list[ToolSpec]:
    global _available_tools

    if _available_tools is None:
        # We need to load tools first
        config = get_config()

        tool_modules: frozenset[str] = frozenset()
        env_tool_modules = config.get_env("TOOL_MODULES", "gptme.tools")

        if env_tool_modules:
            tool_modules = frozenset(env_tool_modules.split(","))

        _available_tools = sorted(_discover_tools(tool_modules))

    return _available_tools


def clear_tools():
    global _available_tools
    global _loaded_tools

    _available_tools = None
    _loaded_tools = []


def get_tools() -> list[ToolSpec]:
    """Returns all loaded tools"""
    return _loaded_tools


def get_tool(tool_name: str) -> ToolSpec | None:
    """Returns a loaded tool by name or block type."""
    # check tool names
    for tool in _loaded_tools:
        if tool.name == tool_name:
            return tool
    # check block types
    for tool in _loaded_tools:
        if tool_name in tool.block_types:
            return tool
    return None


def has_tool(tool_name: str) -> bool:
    """Returns True if a tool is loaded."""
    for tool in _loaded_tools:
        if tool.name == tool_name:
            return True
    return False
